# AEGIS V2 — COMPLETE SYSTEM STRUCTURE
# How It Fires, How It Gathers Data, How It Uses Data
# Everything. Non-Negotiables.
# Generated: 2026-03-08 | Based on 8-Agent Deep Research

---

## 1. THE 30,000-FOOT VIEW

```
┌─────────────────────────────────────────────────────────────────┐
│                        EC2 Instance                              │
│                   c7i-flex.large (4GB RAM)                        │
│                                                                  │
│  ┌─────────────┐    ┌──────────────────────────────────────┐    │
│  │  IB Gateway  │    │         AEGIS V2 Binary               │    │
│  │  (JVM, 2GB)  │◄──►│  (Rust main process, embeds Python)   │    │
│  │  Port 4002   │    │                                       │    │
│  └─────────────┘    │  ┌───────────────────────────────┐   │    │
│                      │  │  Tokio Runtime (2 workers)     │   │    │
│  ┌─────────────┐    │  │  • 1000 market data tasks      │   │    │
│  │    Redis     │    │  │  • Broker I/O task             │   │    │
│  │  (100MB)     │◄──►│  │  • Reconciliation timer        │   │    │
│  │  Cache only  │    │  │  • Health check server         │   │    │
│  └─────────────┘    │  └───────────┬───────────────────┘   │    │
│                      │              │ crossbeam channel      │    │
│                      │              │ (50,000 capacity)      │    │
│                      │  ┌───────────▼───────────────────┐   │    │
│                      │  │  GIL Thread (dedicated)        │   │    │
│                      │  │  Drains channel → Python Brain │   │    │
│                      │  │  200 ticks or 10ms batches     │   │    │
│                      │  └───────────┬───────────────────┘   │    │
│                      │              │ OrderIntent            │    │
│                      │  ┌───────────▼───────────────────┐   │    │
│                      │  │  Executioner (Rust core)       │   │    │
│                      │  │  Risk → WAL → Broker → Exits   │   │    │
│                      │  └──────────────────────────────┘   │    │
│                      └──────────────────────────────────────┘    │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Filesystem                                                │   │
│  │  events/YYYY-MM-DD.ndjson  (WAL — source of truth)        │   │
│  │  config/config.toml        (static configuration)          │   │
│  │  config/dynamic_weights.toml  (nightly Ouroboros output)   │   │
│  │  config/universe_classification.toml  (nightly Universe)   │   │
│  │  config/contracts.toml     (IBKR ConIDs, resolved nightly) │   │
│  │  config/uk_holidays.toml   (bank holiday calendar)         │   │
│  │  config/initial_universe.toml  (day-1 ticker list)         │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. HOW DATA FLOWS — TICK TO TRADE (CHRONOLOGICAL)

### 2.1 Data Gathering (Rust Tokio — 1,000 Concurrent Streams)

```
IBKR Gateway (port 4002)
    │
    │  TCP Socket (TCP_NODELAY = true, H12)
    │
    ▼
Rust: Dynamic Rotation Manager (100 free IBKR lines)
    │
    │  Tier 1: 50 permanent lines (top Vanguard + open positions)
    │  Tier 2: 50 rotating lines (Vanguard warm + Apex, 60s rotation)
    │  Each active sub: reqMktData(ticker) → tick callbacks
    │  Timestamps: u64 nanoseconds from IBKR (H03)
    │  Socket-level recv_timestamp_ns injected for T2T latency (H118)
    │
    │  FILTERS APPLIED AT INGESTION:
    │  • Erroneous tick filter: >5% deviation from 1s MA → drop (H77)
    │  • Price spike filter: verify bid/ask midpoint (H71)
    │  • Synthetic halt: no ticks for 30s → Limp Mode (H122)
    │  • Auction period: 07:50-08:00, 16:30-16:35 → no trading
    │
    ▼
MarketTick struct created:
    { ticker_id: TickerId(u32), bid: f64, ask: f64, last: f64,
      volume: u64, timestamp_ns: u64, recv_timestamp_ns: u64 }
    │
    │  NaN sanitization on every f64 (H09)
    │
    ▼
Crossbeam bounded channel (capacity: 50,000)
    │
    │  OVERFLOW POLICY: Drop OLDEST tick (not newest)
    │  If drops > 100/sec → REDUCE regime
    │  With 100 active lines, burst is ~500 ticks/sec — well within capacity
    │
    ▼
GIL Thread (single dedicated std::thread)
    │
    │  Drains channel continuously
    │  Batches: 200 ticks OR 10ms timeout (whichever first)
    │  Acquires Python GIL ONCE per batch
    │  Categorizes by Vanguard (continuous) vs Apex (60s snapshot)
    │
    │  TOKIO WORKERS NEVER CALL Python::with_gil()
    │  This is the ONLY thread that touches Python
```

### 2.2 Signal Generation (Python Brain — Pure Functions)

```
GIL Thread delivers Vec<MarketTick> to Python
    │
    ├── VANGUARD SNIPER (Tier 1: 50 continuous + Tier 2: 250 rotated):
    │   │  Receives batched ticks every 10ms
    │   │  Runs:
    │   │    • ADX(14) momentum detection
    │   │    • EMA(20) trend confirmation
    │   │    • Volume breakout detection
    │   │    • Moreira-Muir volatility scaling (σ_target / σ_current)
    │   │    • 13-factor Kelly position sizing
    │   │  Output: Option<OrderIntent> or None
    │   │
    │   │  CONSTRAINTS:
    │   │    • Pure function: no side effects, no I/O, no state mutation
    │   │    • Rolling windows only (max 500 bars), no accumulation
    │   │    • Vectorized numpy/pandas, NO .apply() or iterrows() (H60)
    │   │    • Zero-division guards on ALL divisions (H61)
    │   │    • Confidence floor: signal < 65 → discard silently
    │   │
    │   ▼
    │   OrderIntent { ticker_id, side: Long, confidence: 72.5,
    │     strategy: VanguardSniper, kelly_fraction: 0.08,
    │     features: {"adx": 28.5, "rvol": 2.3} }
    │
    └── APEX SCOUT (remaining ~700 tickers, 60s snapshots):
        │  Receives 60-second OHLCV aggregations
        │  Runs RVOL anomaly detection on snapshots
        │  Same constraints as Vanguard
        │  Output: Option<OrderIntent> or None
        │
        ▼
        OrderIntent (same struct, strategy: ApexScout)
```

### 2.3 Risk Check (Executioner — Synchronous, Fail-Closed)

```
OrderIntent crosses PyO3 back to Rust
    │
    ▼
RiskArbiter.evaluate(intent, portfolio_state) → RiskDecision
    │
    │  SYNCHRONOUS. State is FROZEN during evaluation.
    │  ALL checks run in deterministic order (< 1ms total):
    │
    │  CHECK 1:  ISA Safety — side == Short → REJECT (P0, always)
    │  CHECK 2:  Inverse Mutual Exclusion — QQQ3.L open → QQQS.L blocked
    │  CHECK 3:  Duplicate Detection — same (ticker, side) in 60s → REJECT
    │  CHECK 4:  Price Reasonability — >2% from last mid → REJECT
    │  CHECK 5:  Risk Regime — HALT/FLATTEN → REJECT all entries
    │  CHECK 6:  Max Positions — filled + pending >= config.max_positions → REJECT
    │  CHECK 7:  Data Staleness — IBKR timestamp > 120s old → HALT
    │  CHECK 8:  Broker Connected — not connected → HALT
    │  CHECK 9:  WAL Available — can't write → HALT
    │  CHECK 10: Confidence Floor — confidence < 65 → REJECT
    │  CHECK 11: Time-of-Day — after 15:45 London local → REJECT
    │  CHECK 12: Auction Period — 07:50-08:00 or 16:30-16:35 → REJECT
    │  CHECK 13: Spread Veto — real-time spread > 0.5% → REJECT
    │  CHECK 14: Cash Buffer — Available_Cash < Equity × 10% → REJECT
    │  CHECK 15: Portfolio Heat — sum of position risks >= 6% → REJECT
    │  CHECK 16: Sector Heat — sector exposure >= 33% → REJECT
    │  CHECK 17: ISA Annual Limit — cumulative > £20,000 → REJECT
    │  CHECK 18: Daily Drawdown — >2% from high-water → FLATTEN
    │  CHECK 19: Velocity Check — 5+ identical in 1s → drop extras
    │  CHECK 20: Gap Detection — >2% gap → 15min cool-down
    │  CHECK 21: Consecutive Loss — 3 stop-losses today → HALT
    │  CHECK 22: Indicator Warm-up — not warm → REJECT
    │
    │  If REDUCE regime: approved_size = kelly × 0.5
    │  If all checks pass: RiskDecision { approved: true, ... }
    │
    │  EVERY rejection logged with specific VetoReason enum
    │
    ▼
    APPROVED or REJECTED
```

### 2.4 Order Execution (WAL → Broker → Fill → Exit)

```
APPROVED OrderIntent
    │
    ▼
WAL Writer (dedicated OS thread, NOT tokio):
    │  Serialize RoutedOrder event → ndjson line
    │  Append xxHash64 checksum
    │  Append UUIDv7 event_id + dual timestamps
    │  fsync to disk
    │  THIS IS THE POINT OF NO RETURN — crash-safe after this
    │
    ▼
Broker Adapter (async, via tokio):
    │  Create marketable limit order: price = Ask × 1.001
    │  Round to valid tick size (H65)
    │  Inject WAL UUIDv7 into IBKR OrderRef field (H116)
    │  Submit via placeOrder
    │  Rate limiter: token bucket, 45 msg/sec (reserve 5 for emergencies)
    │
    │  Wait for BrokerAck (5 second timeout):
    │    • Accepted → state = ACKNOWLEDGED
    │    • Rejected → state = BROKER_REJECTED → log, WAL update
    │    • Timeout → state = ORPHANED → trigger reconciliation
    │
    ▼
Fill Events (from execDetails, H52):
    │  May be multiple partial fills per order
    │  Each fill: update filled_qty, recalculate VWAP entry price
    │  VWAP = Σ(fill_price × fill_qty) / Σ(fill_qty)
    │  Deduplication by exec_id (prevent double-counting)
    │  Commission tracked from commissionReport (H53)
    │
    │  When remaining_qty == 0 → FILLED
    │
    ▼
Exit Engine registers position:
    │  Calculate initial stop: entry_price × (1 - stop_pct)
    │  Set Chandelier trailing stop rungs
    │  Begin monitoring on EVERY incoming tick
    │
    │  EXIT CONDITIONS (evaluated every tick, all positions):
    │  Priority 1: HALT/FLATTEN → market sell immediately
    │  Priority 2: Hard stop-loss → limit at stop price
    │  Priority 3: Chandelier trailing → Le Beau 1999, 5-rung ladder
    │  Priority 4: Phased EOD flatten → T-35/T-15/T-5 passive exit
    │  Priority 5: Signal reversal → strategy generates opposing signal
    │
    │  COLLISION: highest priority wins. Others suppressed + logged.
    │
    ▼
Exit order submitted → fill → POSITION_CLOSED
    │  Final PnL calculated (FIFO accounting, H87)
    │  PositionClosed event written to WAL
    │  Commission deducted from PnL
```

---

## 3. HOW THE SYSTEM LEARNS — THE 24-HOUR FEEDBACK LOOP

```
                    TRADING DAY
                   08:00 ──────────── 16:30 London local
                    │                    │
                    │  Live trading      │
                    │  WAL records       │
                    │  every event       │
                    │                    │
                    ▼                    ▼
              ┌──────────────────────────────┐
              │     WAL (ndjson journal)      │
              │  events/2026-03-08.ndjson     │
              │  Every order, fill, exit,     │
              │  risk decision, state change  │
              └──────────────┬───────────────┘
                             │
                    23:45 ET │ IBKR Gateway restart
                             │
              ┌──────────────▼───────────────┐
              │    OUROBOROS NIGHTLY PIPELINE  │
              │                               │
              │  Step 1: Ingest WAL           │
              │  Step 2: Bayesian Win Rate    │
              │  Step 3: Deflated Sharpe      │
              │  Step 4: Kelly Recalibration  │
              │  Step 5: Yang-Zhang Vol       │
              │  Step 6: Exit Ladder Calib    │
              │  Step 7: Alpha Decay (IC)     │
              │  Step 8: Universe Reclass     │
              │  Step 9: Walk-Forward Valid   │
              │  Step 10: Output & Verify     │
              │                               │
              │  Outputs:                     │
              │  • dynamic_weights.toml       │
              │  • universe_classification    │
              │  • parameter_history archive  │
              └──────────────┬───────────────┘
                             │
                    00:15 ET │ Gateway comes back
                             │
              ┌──────────────▼───────────────┐
              │    MORNING BOOT (07:50 local)  │
              │                                │
              │  1. Load dynamic_weights.toml  │
              │  2. Load universe_class.toml   │
              │  3. Replay WAL → state         │
              │  4. Reconcile with IBKR        │
              │  5. Resolve orphans            │
              │  6. Warm up indicators         │
              │  7. Begin trading at 08:00     │
              └────────────────────────────────┘
```

**The loop tightens from 7 days to 24 hours.** Yesterday's lessons
become tomorrow's weapon. The system that trades on Day 30 is
fundamentally different from the one on Day 1.

---

## 4. THE ORDER STATE MACHINE — 15 STATES

```
  INTENT_GENERATED ──── Python outputs OrderIntent
       │
       ▼
  RISK_CHECKED ──────── RiskArbiter evaluates (< 1ms)
       │
       ├── REJECTED ─── (Terminal) Logged with VetoReason
       │
       ▼
  WAL_WRITTEN ───────── RoutedOrder in ndjson, fsync'd
       │
       ▼
  SUBMITTED ─────────── Order sent to IBKR
       │
       ├── BROKER_REJECTED ── (Terminal) IBKR said no
       │
       ├── ORPHANED ───────── No ack in 5s → reconcile
       │        │
       │        └── reqOpenOrders → diff → resolve
       │
       ▼
  ACKNOWLEDGED ──────── IBKR confirmed receipt
       │
       ▼
  PARTIALLY_FILLED ──── 0+ partial fills (VWAP updates each)
       │
       ▼
  FILLED ────────────── remaining_qty == 0
       │
       ▼
  EXIT_REGISTERED ───── Stop-loss + trailing stop active
       │
       ▼
  EXIT_TRIGGERED ────── Price breach / time / risk signal
       │
       ▼
  EXIT_ORDER_SUBMITTED  Exit order sent to IBKR
       │
       ▼
  EXIT_FILLED ───────── Exit order filled
       │
       ▼
  POSITION_CLOSED ───── (Terminal) Final PnL calculated
```

---

## 5. THE RISK REGIME HIERARCHY

```
  ┌─────────┐
  │  HALT   │ ← Data stale >120s, broker disconnect, WAL fail,
  │ (kill)  │   ISA violation, 3 rejections/min, 3 stop-losses/day
  │         │   RECOVERY: Manual human approval only
  └────┬────┘
       │ (highest precedence)
  ┌────▼────┐
  │ FLATTEN │ ← Daily loss >2%, orphaned order, recon mismatch
  │ (unwind)│   RECOVERY: Auto after all positions closed + clean
  └────┬────┘
       │
  ┌────▼────┐
  │ REDUCE  │ ← Tick drops >100/s, queue >80%, Python >2000ms
  │ (defend)│   RECOVERY: Auto after triggers clear for 5 min
  └────┬────┘
       │
  ┌────▼────┐
  │ NORMAL  │ ← All systems nominal. Full Kelly. All strategies.
  │ (trade) │
  └─────────┘

  COLLISION: Higher state ALWAYS wins. HALT + REDUCE = HALT.
```

---

## 6. DIRECTORY STRUCTURE

```
nzt48-aegis-v2/
├── Cargo.toml                    # Workspace root
├── rust_core/
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs                # Crate root, PyO3 module
│       ├── types.rs              # All #[pyclass] structs + enums
│       ├── ffi.rs                # PyO3 function exports
│       ├── risk_arbiter.rs       # Synchronous risk gate (≤400 LOC)
│       ├── wal_writer.rs         # Dedicated OS thread WAL (≤400 LOC)
│       ├── order_manager.rs      # Order lifecycle + reaper (≤400 LOC)
│       ├── exit_engine.rs        # Singular exit authority (≤400 LOC)
│       ├── broker.rs             # Async trait + PaperBroker (≤400 LOC)
│       ├── reconciler.rs         # Position reconciliation (≤300 LOC)
│       ├── clock.rs              # IBKR clock + timezone (≤200 LOC)
│       ├── universe.rs           # Ticker routing + classification
│       ├── channel.rs            # Crossbeam channel + monitoring
│       ├── config.rs             # TOML config loading
│       └── main.rs               # Entrypoint, tokio runtime, startup
├── python_brain/
│   ├── pyproject.toml
│   └── brain/
│       ├── __init__.py
│       ├── strategies/
│       │   ├── __init__.py
│       │   ├── vanguard_sniper.py  # Momentum, pure function
│       │   └── apex_scout.py       # RVOL anomaly, pure function
│       ├── sizing/
│       │   ├── __init__.py
│       │   └── kelly_13factor.py   # 13-factor Kelly sizing
│       └── tests/
├── ouroboros/
│   ├── __init__.py
│   ├── pipeline.py               # 10-step nightly pipeline
│   ├── bayesian.py               # Win rate, DSR, priors
│   ├── volatility.py             # Yang-Zhang estimator
│   ├── exit_calibration.py       # MAE/MFE analysis
│   ├── alpha_decay.py            # IC tracking
│   ├── universe.py               # ASER ranking, reclassification
│   ├── walk_forward.py           # Walk-forward validation
│   └── tests/
├── config/
│   ├── config.toml               # Static configuration
│   ├── initial_universe.toml     # Day-1 ticker list (~1000)
│   ├── uk_holidays.toml          # Bank holiday calendar
│   ├── contracts.toml            # IBKR ConIDs (generated)
│   ├── dynamic_weights.toml      # Ouroboros output (generated)
│   ├── universe_classification.toml  # Nightly output (generated)
│   └── parameter_history/        # Archived nightly params
├── events/                       # WAL journal files
│   └── YYYY-MM-DD.ndjson
├── dead_letter/                  # Unparseable OrderIntents
├── docs/
│   ├── 00_CANONICAL_RULES.md
│   ├── 01_DATA_CONTRACTS.md
│   ├── 02_STATE_MACHINE.md
│   ├── 03_ACCEPTANCE_TESTS.md
│   ├── REBUILD_MANIFEST.md
│   ├── BLIND_SPOTS.md
│   ├── SYSTEM_STRUCTURE.md
│   └── checkpoints/
│       ├── PHASE_0_GATE.md
│       ├── PHASE_1_GATE.md
│       └── ...
├── scripts/
│   ├── synthetic_data_gen.py     # 1M tick generator (H97)
│   └── backup_wal.sh             # Daily S3 WAL backup
├── .claudeignore                 # target/, data/, node_modules/
├── rust-toolchain.toml           # Locked Rust version
└── EXECUTION_STATE.md            # Progress tracking
```

---

## 7. THE NON-NEGOTIABLES — COMPLETE LIST

### Architecture Non-Negotiables
1. **Hexagonal Pipeline**: Input → Brain → Vault → Broker. No God Object.
2. **Python Has No Gun**: Python outputs OrderIntent. Rust decides everything.
3. **GIL Isolation**: Tokio workers NEVER call `Python::with_gil()`.
4. **WAL Is God**: Redis is cache. ndjson journal is truth. WAL wins all disputes.
5. **Fail-Closed Only**: Unknown state → HALT. Never guess.
6. **No Live Learning**: ALL ML/adaptation runs offline in Ouroboros.
7. **Singular Exit Authority**: ONE exit engine. Priority hierarchy resolves collisions.
8. **ISA Safety Invariant**: NEVER short sell. Checked on EVERY order.

### Data Non-Negotiables
9. **Timestamps as u64 nanoseconds**: IBKR server time, not system clock.
10. **Timezone-Aware**: All LSE times in Europe/London, not UTC offsets.
11. **Enums Not Strings**: Direction, StrategyId, VetoReason cross FFI as enums.
12. **NaN Sanitization**: Every f64 from Python checked for NaN/Infinity.
13. **Option Not NaN**: Use `Option<f64>` for missing data, not NaN.
14. **Batch FFI**: 200 ticks or 10ms, never single-tick Python calls.
15. **Immutable Clones**: Python receives `.clone()`, never references.

### Risk Non-Negotiables
16. **3-Position Limit**: Filled + pending combined (configurable for Crucible = 1).
17. **120s Stale Threshold**: IBKR timestamp, not wall clock → HALT.
18. **2% Daily Drawdown**: From intraday high-water → FLATTEN.
19. **Portfolio Heat < 6%**: Sum of (entry-stop)/equity across positions.
20. **Kelly Clamp 0.20**: Never bet more than 20% regardless of math.
21. **Kelly ÷ Leverage**: Divide by 3 for 3x, by 5 for 5x ETPs.
22. **ISA £20,000 Annual Limit**: Tracked in WAL, enforced by RiskArbiter.

### Execution Non-Negotiables
23. **Marketable Limit Orders**: Ask × 1.001, never raw Market.
24. **Market-to-Limit for Emergency**: MTL for HALT exits (H117).
25. **Tick Size Rounding**: £0.001 under £1, £0.01 over £1 (H65).
26. **Rate Limiter**: 45 msg/sec to IBKR, reserve 5 for emergencies.
27. **Stale Order Reaper**: Cancel orders older than 120 seconds.
28. **Duplicate Detection**: Reject same (ticker, side) within 60 seconds.

### Operational Non-Negotiables
29. **panic = "abort"**: In Cargo.toml release profile (H06).
30. **jemalloc**: Use tikv-jemallocator, not system allocator (H14).
31. **No .unwrap() in Hot Path**: Clippy deny rule (H15).
32. **No Stubs**: Every `// TODO` or `unimplemented!()` = Phase failure.
33. **400 Line File Limit**: Refactor into submodules if exceeded.
34. **Proof Before Progress**: Actual terminal output in every gate.

---

## 8. COST SUMMARY

### The Crucible (Days 1-30):
| Item | Cost |
|------|------|
| EC2 c7i-flex.large | ~$62/mo |
| Dynamic rotation (100 free IBKR lines) | $0/mo |
| LSE data subscription | $0 (API streams via reqMktData) |
| **Total** | **~$62/mo** |

### The Expansion (Months 1-6):
| Item | Cost |
|------|------|
| EC2 m7i-flex.large (8GB) eu-west-2 | ~$124/mo |
| Same rotation model (100 free lines) | $0/mo |
| LSE data subscription | $0 (API streams via reqMktData) |
| **Total** | **~$124/mo** |

---

## 9. BUILD SEQUENCE (10 PHASES)

```
Phase 0: SPEC LOCK          → 4 spec documents, gold-standard
Phase 1: SKELETON + FFI     → Rust types + PyO3 bridge
Phase 2: RISK VAULT         → RiskArbiter 4-state hierarchy
Phase 3: EVENT JOURNAL      → WAL + crash recovery
Phase 4: BROKER ADAPTER     → async trait + PaperBroker
Phase 5: EXIT ENGINE        → Chandelier 5-rung + priority
Phase 6A: UNIVERSE          → 1,000 ticker routing
Phase 6B: QUANTUM BRAIN     → Python strategies (pure functions)
Phase 6C: KELLY + WIRING    → 13-factor sizing + full pipeline
Phase 7: REPLAY HARNESS     → Synthetic data + determinism
Phase 8: PAPER BOOTSTRAP    → Live IBKR connection + recon
Phase 9: OUROBOROS           → 10-step nightly analytics
```

Each phase has a CHECKPOINT GATE requiring human approval.
No phase proceeds without approval. No gates are forged.

---

## 10. WHAT MAKES THIS INSTITUTIONAL-GRADE

| Capability | Retail Systems | AEGIS V2 |
|------------|---------------|----------|
| Risk checks | Simple position limits | 22-check synchronous gate |
| Event sourcing | None (lost on crash) | Append-only WAL, crash recovery |
| Kelly sizing | Basic fraction | 13-factor with leverage, vol, regime |
| Exit management | Single stop-loss | 5-rung Chandelier + priority hierarchy |
| Nightly learning | None | 10-step Ouroboros with walk-forward |
| Alpha decay | None | IC tracking with graduated response |
| Reconciliation | None | Fill-triggered + 5-min polling |
| Timezone handling | Hardcoded | chrono-tz Europe/London, DST-aware |
| Order lifecycle | Fire and forget | 15-state machine with orphan recovery |
| Volatility estimation | Simple std dev | Yang-Zhang (2000) optimal estimator |
