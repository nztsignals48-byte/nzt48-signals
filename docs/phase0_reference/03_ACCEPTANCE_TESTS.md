# 03 — ACCEPTANCE TESTS
# AEGIS V2 — Institutional Trading Engine
# Version: 1.0 | Status: SPEC LOCK
# Definition of Done for EVERY phase (0-9).
# A phase is NOT complete until ALL its acceptance tests pass.
# Tests must produce ACTUAL terminal output, not summaries.

---

## PHASE 0 — SPEC LOCK

Phase 0 passes when ALL of the following are true:

- [ ] `docs/00_CANONICAL_RULES.md` exists and contains >= 23 named rules with exact thresholds
- [ ] `docs/01_DATA_CONTRACTS.md` exists and defines ALL 7 core structs with every field and Rust type
- [ ] `docs/01_DATA_CONTRACTS.md` defines ALL 10 enums (Direction, StrategyId, VetoReason, RiskRegime, BrokerAckStatus, ExitReason, ExitPriority, ExitOrderType, OrderState, WalEventType)
- [ ] `docs/02_STATE_MACHINE.md` exists and maps ALL 15 order lifecycle states with transitions
- [ ] `docs/02_STATE_MACHINE.md` includes orphan recovery protocol with 4 resolution cases
- [ ] `docs/02_STATE_MACHINE.md` includes phantom fill handling (H55)
- [ ] `docs/02_STATE_MACHINE.md` includes partial fill accumulation with VWAP math
- [ ] `docs/02_STATE_MACHINE.md` includes startup recovery sequence (8 steps)
- [ ] `docs/02_STATE_MACHINE.md` includes exit priority collision matrix
- [ ] `docs/02_STATE_MACHINE.md` includes RiskArbiter state machine with transition rules
- [ ] `docs/03_ACCEPTANCE_TESTS.md` exists and defines tests for ALL phases (0-9)
- [ ] `tree nzt48-aegis-v2/` output shows correct directory structure
- [ ] `wc -l docs/*.md` shows all files are non-empty
- [ ] ZERO citation artifacts ([cite_start], [N], etc.) in any file
- [ ] Constants registry in 00_CANONICAL_RULES.md contains valid TOML

---

## PHASE 1 — EXECUTIONER SKELETON + FFI

Phase 1 passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings in rust_core workspace
- [ ] `cargo test` passes with ZERO failures
- [ ] ALL 7 data contract structs compile as `#[pyclass]` types
- [ ] ALL 10 enums compile with correct derive macros
- [ ] Python can import the Rust module: `from rust_core import MarketTick, OrderIntent, Direction`
- [ ] Round-trip test: create MarketTick in Rust → pass to Python → read fields → pass back to Rust → all fields match exactly
- [ ] Round-trip test covers ALL 7 struct types
- [ ] TickerId(u32) newtype enforces no raw String ticker comparisons
- [ ] NaN sanitization: pass f64::NAN from Python → Rust catches it → returns BrainError::InvalidTick
- [ ] Struct packing: fields ordered largest-to-smallest (H128)
- [ ] `maturin develop` builds the Python wheel successfully
- [ ] pytest passes with ZERO failures
- [ ] `.claudeignore` contains target/, data/, node_modules/ (H89)
- [ ] `rust-toolchain.toml` locks exact Rust compiler version (H92)
- [ ] `docs/BLIND_SPOTS.md` lists 3 uncertainties about IBKR API or PyO3 (H107)

---

## PHASE 2 — EXECUTIONER RISK VAULT

Phase 2 passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings
- [ ] `cargo test` passes with ZERO failures
- [ ] PortfolioState tracks positions, cash, PnL correctly
- [ ] RiskArbiter implements all 4 states: HALT > FLATTEN > REDUCE > NORMAL
- [ ] Precedence collision test: trigger HALT + REDUCE simultaneously → HALT wins
- [ ] Precedence collision test: trigger FLATTEN + REDUCE simultaneously → FLATTEN wins
- [ ] ISA invariant test: attempt side=Short with qty=0 → REJECTED with VetoReason::IsaShortSellBlocked
- [ ] Drawdown test: simulate 2.1% loss from intraday high-water → FLATTEN activates
- [ ] Data staleness test: set last_tick to 121s ago → HALT activates
- [ ] Spread veto test: spread = 0.6% → REJECTED with VetoReason::SpreadTooWide
- [ ] Time cutoff test: time = 15:46 → REJECTED with VetoReason::TooLateInSession
- [ ] Consecutive loss test: 3 stop-losses → HALT with VetoReason::ConsecutiveLossBreaker
- [ ] Inverse exclusion test: QQQ3.L open, attempt QQQS.L → REJECTED with VetoReason::InverseMutualExclusion
- [ ] Velocity test: 5 identical intents in 1s → only first passes, 4 dropped
- [ ] Max positions test: 3 positions filled, attempt 4th → REJECTED
- [ ] Pending + filled test: 2 filled + 1 pending, attempt 4th → REJECTED (H34)
- [ ] Cash buffer test: available_cash = 9% of equity → REJECTED (H31)
- [ ] Sector heat test: semiconductor positions = 34% → REJECTED (H30)
- [ ] Portfolio heat test: total risk = 6.1% → REJECTED
- [ ] VetoReason logging test: each rejection logs the specific threshold breached (H39)
- [ ] proptest: random chaotic state transitions → no panics (H91)
- [ ] #![deny(clippy::unwrap_used)] enforced in crate root (H15)

---

## PHASE 3 — CANONICAL EVENT JOURNAL + RECOVERY

Phase 3 passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings
- [ ] `cargo test` passes with ZERO failures
- [ ] Append 100 events to WAL → kill process → restart → replay → PortfolioState matches exactly
- [ ] Every WAL line contains: event_id (UUIDv7), schema_version, event_time_ns, write_time_ns, checksum (CRC32), payload
- [ ] Corrupt last line (truncate mid-JSON) → replay skips it with WARNING, state consistent
- [ ] Corrupt non-last line → panic! and refuse to trade (H27)
- [ ] CRC32 mismatch on non-last line → panic! and refuse to trade
- [ ] Orphan simulation: write RoutedOrder with no BrokerAck → on replay, marked ORPHANED → new orders blocked
- [ ] Snapshot + replay: write 1000 events, write snapshot, write 100 more → restart → load snapshot + replay 100 (not 1100)
- [ ] Idempotent replay: replay same WAL twice → identical PortfolioState (H84)
- [ ] Hourly state hash: hash written to WAL, verified on replay (H85)
- [ ] Disk space check: mock < 5% disk → FLATTEN + HALT (H25)
- [ ] WAL writer uses &Event (immutable borrow, H26)
- [ ] WAL writer runs in tokio::task::spawn_blocking (H13)
- [ ] --replay CLI flag works (H28)
- [ ] Dead letter queue: unparseable OrderIntent → dead_letter/YYYY-MM-DD.ndjson (H81)

---

## PHASE 4 — BROKER INTERFACE + PAPER ADAPTER

Phase 4 passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings
- [ ] `cargo test` passes with ZERO failures
- [ ] BrokerAdapter trait defined with: submit_order, cancel_order, request_positions, request_open_orders, heartbeat, is_connected
- [ ] PaperBroker implements BrokerAdapter
- [ ] Full lifecycle test: submit → ack → fill → position updated in PortfolioState
- [ ] Partial fill test: order for 100 shares, fill 37, then 63. Final qty=100, price=VWAP
- [ ] Duplicate submission: same order_id twice → second rejected
- [ ] Heartbeat timeout: mock failure for 60s → HALT triggered
- [ ] PaperBroker simulates configurable latency (50-200ms)
- [ ] PaperBroker generates valid UUIDv7 exec_ids for fills
- [ ] PaperBroker supports random partial fills (configurable)
- [ ] Rate limiter: token bucket at 50 msgs/sec (H16)
- [ ] Exponential backoff test: disconnect → reconnect attempts at 1s, 2s, 4s, 8s (H17)
- [ ] Client ID isolation: Executioner=100, Ouroboros=200 (H41)
- [ ] nextValidId persistence in WAL (H47)
- [ ] PendingCancel state: cancel sent → wait for Cancelled ack (H54)
- [ ] Phantom fill: cancel sent, fill arrives 50ms later → accept position (H55)

---

## PHASE 5 — SINGULAR CANONICAL EXIT ENGINE

Phase 5 passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings
- [ ] `cargo test` passes with ZERO failures
- [ ] Exactly ONE exit engine exists (no duplicate exit logic anywhere)
- [ ] Exit types with correct priority: HALT > Hard Stop > Chandelier > EOD > Signal
- [ ] Same-tick collision: hard stop + Chandelier fire → ONLY hard stop fires
- [ ] Same-tick collision: hard stop + EOD fire → ONLY hard stop fires
- [ ] HALT override: hard stop fires, then HALT fires → ALL exits become market sell
- [ ] Chandelier 5-rung ladder test:
  - Entry at 10.00, ATR = 0.50
  - Price rises to 10.25 (+0.5 ATR) → stop ratchets to 10.00 (breakeven, Rung 1)
  - Price rises to 10.50 (+1.0 ATR) → stop ratchets to 10.125 (+0.25 ATR, Rung 2)
  - Price rises to 10.75 (+1.5 ATR) → stop ratchets to 10.25 (+0.5 ATR, Rung 3)
  - Price rises to 11.00 (+2.0 ATR) → stop ratchets to 10.50 (+1.0 ATR, Rung 4)
  - Price rises to 11.50 (+3.0 ATR) → stop trails at 11.50 - 0.75 = 10.75 (Rung 5)
- [ ] Stop ratchet: stop can NEVER decrease (H68)
- [ ] EOD flatten: time reaches 16:25 → all positions get market sell
- [ ] Shadow stops: stops are internal in Rust, NOT native IBKR trailing stops (H67)
- [ ] TIF rules: entry=DAY, emergency=IOC (H69)
- [ ] MTL for emergency exits (H117)
- [ ] Highest_high persisted in WAL for crash recovery (H70)
- [ ] Price spike filter: 10% drop + instant bounce → verify midpoint before triggering (H71)
- [ ] Commission in targets: reject trade if EV < 0 after commission (H73)
- [ ] ExitStrategy trait for hot-swappable exit math (H72)

---

## PHASE 6A — UNIVERSE: RUST DATA ROUTING

Phase 6A passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings
- [ ] `cargo test` passes with ZERO failures
- [ ] Feed 1,000 synthetic tickers → Vanguard (300) gets continuous delivery
- [ ] Feed 1,000 synthetic tickers → Apex (700) gets 60-second OHLCV snapshots
- [ ] NO tick is routed to both Vanguard AND Apex paths simultaneously
- [ ] Amihud illiquidity filter: inject illiquid ticker (ILLIQ > threshold) → filtered out
- [ ] ASER filter: inject ticker with spread > 0.5% → filtered out
- [ ] TickerId interning: "QQQ3.L" → TickerId(42), no String comparison in hot path (H01)
- [ ] Crossbeam channel: bounded at 50,000 capacity
- [ ] Oldest-tick dropping: fill channel to capacity → oldest dropped, newest preserved
- [ ] Drop rate monitoring: >100 drops/sec → REDUCE escalation
- [ ] Queue depth monitoring: 40,000 → REDUCE, 50,000 → HALT
- [ ] tokio::time::interval for 60s Apex snapshots (H18, drift-resistant)
- [ ] reqMktData pacing: 10ms spacing between requests (H42)
- [ ] Synthetic halt detection: no ticks for 30s on specific ticker → cancel open orders (H122)
- [ ] Reverse split detection: >500% overnight price move → HALT ticker (H76)
- [ ] Erroneous tick filter: >5% deviation from 1s MA → filtered (H77)

---

## PHASE 6B — QUANTUM BRAIN: PYTHON STRATEGIES

Phase 6B passes when ALL of the following are true:

- [ ] pytest passes with ZERO failures
- [ ] Vanguard Sniper: feed identical tick batches twice → identical OrderIntent output (deterministic)
- [ ] Apex Scout: feed identical snapshots twice → identical OrderIntent output (deterministic)
- [ ] Empty tick list → returns None (no crash, no error)
- [ ] Single tick → valid processing (no crash)
- [ ] Confidence floor: signal with confidence=64 → filtered (returns None)
- [ ] Moreira-Muir: higher realized volatility → smaller position size (inverse scaling)
- [ ] Pure function verification:
  - No imports of ib_insync, ibapi, or any broker library
  - No global variables
  - No file I/O, no network I/O, no database queries
  - No state mutation outside of local function scope
  - No asyncio, no threading, no concurrent.futures (H07)
- [ ] No .apply() or iterrows() in any Pandas code (H60)
- [ ] Zero-division guards: np.where(denom == 0, 1e-9, denom) on ALL divisions (H61)
- [ ] All logging via PyO3 channel back to Rust, not Python file I/O (H08)
- [ ] Error masking ban: no `except Exception as e: pass` anywhere (H108)
- [ ] No magic numbers: all constants reference the config (H109)
- [ ] Correlation on log returns, not raw prices (H63)

---

## PHASE 6C — KELLY SIZING + FFI WIRING

Phase 6C passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings
- [ ] `cargo test` passes with ZERO failures
- [ ] pytest passes with ZERO failures
- [ ] 12-factor Kelly implemented with all factors documented:
  1. Base Kelly from Bayesian WR
  2. Volatility decay (3x: ×9, 5x: ×25)
  3. Moreira-Muir realized vol scaling
  4. Correlation penalty
  5. Drawdown scaling
  6. Amihud liquidity scaling
  7. Regime scaling
  8. Spread cost adjustment
  9. Time-of-day scaling
  10. Confidence scaling
  11. Half-Kelly cap (0.5)
  12. Portfolio heat limit (6%)
- [ ] Kelly determinism: identical inputs → identical output
- [ ] Kelly cap: high confidence → capped at half-Kelly (0.5)
- [ ] Kelly clamp: even with cap=0.5, output ≤ 0.20 (H57)
- [ ] Portfolio heat: 3 positions at 2.1% each → new order rejected (>6%)
- [ ] Volatility drag: 3x ETP → variance × 9 in calculation (H59)
- [ ] Bayesian shrinkage: W=60% over 10 trades → adjusted downward (H58)
- [ ] Outlier win cap: single trade at 5% → capped at 3% for Kelly avg (H62)
- [ ] Fractional shares: always math.floor(), never round() (H64)
- [ ] Full pipeline end-to-end:
  - Synthetic tick → crossbeam channel → GIL Thread batch →
  - Python Vanguard → OrderIntent → RiskArbiter → WAL event →
  - PaperBroker fill → PositionState updated → Exit Engine armed
- [ ] GIL isolation verified: tokio workers NEVER call Python::with_gil()
- [ ] Batch FFI: 100-200 ticks per Python call, not individual ticks
- [ ] Backpressure: Python >500ms → WARNING logged; >2000ms → REDUCE

---

## PHASE 7 — REPLAY HARNESS + PERFECT WIRING

Phase 7 passes when ALL of the following are true:

- [ ] `cargo check` passes with ZERO warnings
- [ ] `cargo test` passes with ZERO failures
- [ ] pytest passes with ZERO failures
- [ ] Replay 1 full day of synthetic data at 10x speed
- [ ] Every OrderIntent that passes risk → appears in WAL → appears in broker → has fill or reject → exit registered
- [ ] ZERO disconnected signal paths: count signals in == events out
- [ ] ZERO orphaned state: after replay, PortfolioState == sum of all WAL events
- [ ] Deterministic replay: same day twice → identical WAL output
- [ ] Network failure injection mid-replay → HALT activates → no orders lost → state recoverable
- [ ] Gap detection: inject >2% gap → 15-minute cool-down enforced (H66)
- [ ] Erroneous tick handling: inject spike → filtered before stop-loss triggers (H77)
- [ ] Price spike filter: inject flash crash → midpoint verification prevents false exit (H71)
- [ ] Synthetic halt: inject 30s no-tick for one ticker → open orders cancelled (H122)
- [ ] T2T latency logging: recv_timestamp_ns to RoutedOrder timestamp logged (H118)
- [ ] Memory stability: run 1,000,000 ticks through pipeline → memory flat (H98)

---

## PHASE 8 — PAPER ENGINE BOOTSTRAP

Phase 8 passes when ALL of the following are true:

- [ ] Engine starts and connects to IB Gateway on EC2 (port 4002)
- [ ] reqCurrentTime() → clock offset computed, logged
- [ ] 1,000 tickers subscribed via reqMktData (paced at 10ms, H42)
- [ ] First tick received and processed through full pipeline
- [ ] Market data type = 3 (Delayed) for paper mode (H120)
- [ ] Position reconciliation runs every 5 minutes
- [ ] Reconciliation test: manually create position in IBKR → engine detects mismatch → logs CRITICAL
- [ ] Startup sequence executes all 8 steps in correct order
- [ ] Restart recovery test:
  1. Start engine, let it run for 5 minutes
  2. kill -9 the process
  3. Restart and verify:
     - WAL replays correctly
     - Positions match IBKR
     - Market data resumes
     - No duplicate orders submitted
     - SystemReady event written to WAL
- [ ] Contract details loaded from contracts.toml (H48)
- [ ] Marketable limit orders: Ask + 0.1% (H49)
- [ ] Stop trigger = Last Price (H50)
- [ ] OUTSIDE_RTH = false (H51)
- [ ] Error 1100 handling: disconnect → HALT (H43)
- [ ] Error 1102 handling: reconnect → orphan reconciliation (H44)
- [ ] Error 321 handling: pacing violation → 5s backoff (H46)
- [ ] Historical data pacing: ≤60 requests per 10 minutes (H125)
- [ ] Gateway JVM tuned: -XX:+UseZGC -Xmx2G (H119)
- [ ] systemd service file created for auto-restart (H74)
- [ ] IS_LIVE = false hardcoded (H20)
- [ ] File descriptors: ulimit -n 65535 (H80)
- [ ] Swap disabled: swapoff -a (H131)

---

## PHASE 9 — OUROBOROS NIGHTLY ANALYTICS + UNIVERSE RECLASSIFICATION

Phase 9 passes when ALL of the following are true:

- [ ] pytest passes with ZERO failures
- [ ] `cargo test` passes with ZERO failures
- [ ] Nightly timing: Ouroboros refuses to run during LSE hours (08:00-16:30)
- [ ] Feed 100 synthetic trades → Bayesian WR converges (Laplace smoothing)
- [ ] Feed trades with known Sharpe ratio → DSR calculation matches:
  DSR = Φ((SR* - SR₀) / σ_SR₀) where σ_SR₀ = √((1 - γ₃·SR₀ + ((γ₄-1)/4)·SR₀²) / (T-1))
- [ ] dynamic_weights.toml is valid TOML and parseable
- [ ] universe_classification.toml is valid TOML and parseable
- [ ] Reproducibility: run Ouroboros twice on same WAL → identical .toml output
- [ ] Kelly Accelerator: feed winning trades for a ticker → Kelly fraction INCREASES in output .toml
- [ ] Exit Calibration: feed trades that consistently hit Rung 5 → Chandelier multiplier LOOSENS in output .toml
- [ ] Regime Hunting: feed trades with known regime labels → profitable regimes identified
- [ ] Alpha Sieve: ticker with widening spreads → demoted from Vanguard in universe_classification.toml
- [ ] Quarantine rules verified:
  - Ouroboros NEVER writes to live WAL
  - Ouroboros NEVER influences live decisions in-session
  - Ouroboros reads ONLY the finished day's journal
- [ ] Morning boot sequence: Executioner loads .toml artifacts atomically
- [ ] Safe fallback: if Ouroboros fails → yesterday's .toml files used
- [ ] Client ID isolation: Ouroboros uses clientId=200 (H41)
- [ ] Nightly timeline verified:
  - 23:45 ET → Gateway restart detected
  - 23:46 ET → Universe reclassification runs
  - 23:50 ET → Ouroboros analytics runs
  - 00:00 ET → StateSnapshot written to WAL
  - 00:15 ET → Gateway back online
  - 00:16 ET → Clock re-sync (reqCurrentTime)

---

## CROSS-PHASE INVARIANTS (MUST HOLD AT ALL TIMES)

These invariants are checked after EVERY phase gate:

- [ ] NO banned names (S3, S8, S15, S16) anywhere in the codebase
- [ ] NO `// TODO`, `pass`, or `unimplemented!()` in production code (H103)
- [ ] NO `.unwrap()` or `.expect()` in the Executioner hot path (H15)
- [ ] NO `except Exception as e: pass` in Python (H108)
- [ ] NO magic numbers (other than 0 or 1) without named constants (H109)
- [ ] NO unsafe keyword except for PyO3 FFI with 3-paragraph justification (H96)
- [ ] NO global state (lazy_static/OnceCell) except config/logging (H112)
- [ ] NO file exceeds 400 lines without refactoring into submodules (H105)
- [ ] cargo fmt produces no changes (H93)
- [ ] ruff check produces no errors (H93)
- [ ] ALL thinking blocks include the compliance JSON (H113)
- [ ] Every Checkpoint Gate contains ACTUAL terminal output, not summaries
