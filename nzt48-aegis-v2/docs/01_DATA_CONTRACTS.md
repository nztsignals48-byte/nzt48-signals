# 01 — DATA CONTRACTS
# AEGIS V2 — Institutional Trading Engine
# Version: 1.0 | Status: SPEC LOCK
# Every type crossing the Rust ↔ Python FFI boundary is defined here.
# ALL fields are mandatory. NO optional fields unless marked Option<T>.
# These are the EXACT Rust structs. Python sees them via #[pyclass].

---

## NEWTYPES (Type Safety at Compile Time)

```rust
/// Interned ticker identifier. Never use String for ticker comparisons.
/// Map at Universe boundary: "QQQ3.L" → TickerId(42)
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[pyclass]
pub struct TickerId(pub u32);

/// UUIDv7, time-ordered, sortable. Used for all event + order IDs.
/// Persisted in WAL. Injected into IBKR OrderRef field for crash recovery (H116).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct OrderId(pub uuid::Uuid);
```

---

## ENUMS

```rust
/// Order direction. NEVER use strings ("BUY"/"SELL") across FFI (H04).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[pyclass]
pub enum Direction {
    Long,
    // Short is defined but ISA safety invariant will ALWAYS reject it.
    // Exists for type completeness + inverse ETP representation.
    Short,
}

/// Strategy identifier. Banned names (S3, S8, S15, S16) never appear.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[pyclass]
pub enum StrategyId {
    VanguardSniper,  // Top 300 ultra-liquid, continuous momentum
    ApexScout,       // Remaining 700, 60s RVOL anomaly scanner
}

/// Why the RiskArbiter vetoed a trade. Logged with every rejection (H39).
#[derive(Clone, Debug, PartialEq, Eq)]
#[pyclass]
pub enum VetoReason {
    Approved,                        // Not a veto — trade passed
    MaxPositionsReached,             // 3 filled + pending (H34)
    PortfolioHeatExceeded,           // Total risk > 6%
    SectorHeatExceeded { sector: String, pct: u32 }, // >33% (H30)
    CashBufferInsufficient,          // <10% available (H31)
    DailyDrawdownBreached,           // >2% from high-water (H29)
    StaleData { age_secs: u64 },     // >120s (IBKR timestamp)
    BrokerDisconnected,
    WalUnavailable,
    IsaShortSellBlocked,             // P0 ISA violation
    InverseMutualExclusion { blocker: TickerId }, // H32
    SpreadTooWide { spread_pct: f64 }, // >0.5% (H36)
    TooLateInSession,                // After 15:45 LSE (H35)
    VelocityCheckTriggered,          // 5+ identical in 1s (H37)
    AuctionPeriod,                   // 07:50-08:00 or 16:30-16:35
    GapDetected { gap_pct: f64 },    // >2% gap (H66)
    ConfidenceBelowFloor { confidence: f64 }, // <65
    QueueDepthCritical { depth: usize },
    ConsecutiveLossBreaker,          // 3 stop-losses today (H38)
    RejectToHalt,                    // 3 IBKR rejects in 1 min (H88)
    BackpressureCritical,            // Python batch >2000ms
}

/// Risk Arbiter regime. Strict hierarchy: HALT > FLATTEN > REDUCE > NORMAL.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[pyclass]
pub enum RiskRegime {
    Normal  = 0,
    Reduce  = 1,
    Flatten = 2,
    Halt    = 3,
}

/// Broker acknowledgement status.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[pyclass]
pub enum BrokerAckStatus {
    Accepted,        // IBKR accepted the order
    Rejected,        // IBKR rejected (invalid contract, pacing, etc.)
    PendingCancel,   // Cancel sent, awaiting confirmation (H54)
    Cancelled,       // IBKR confirmed cancellation
}

/// Why an exit was triggered.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[pyclass]
pub enum ExitReason {
    HaltFlatten,          // RiskArbiter HALT or FLATTEN
    HardStopLoss,         // Price breach below stop price
    ChandelierTrailing,   // Le Beau 1999, 5-rung ladder
    EodFlatten,           // 16:25 LSE time-based exit
    SignalReversal,       // Strategy generates opposing signal
    SyntheticHalt,        // No ticks for 30s on ticker (H122)
    ReverseSplitSuspected, // >500% overnight price move (H76)
}

/// Exit priority. Higher number = higher priority. Enum ordering matches.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[pyclass]
pub enum ExitPriority {
    SignalReversal  = 1,
    EodFlatten      = 2,
    ChandelierStop  = 3,
    HardStopLoss    = 4,
    HaltFlatten     = 5,  // Highest — overrides everything
}

/// Order type for exit execution.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[pyclass]
pub enum ExitOrderType {
    MarketSell,          // HALT/FLATTEN emergency
    MarketToLimit,       // MTL for controlled emergency exits (H117)
    LimitAtStop,         // Hard stop-loss (limit at stop price)
}

/// Order lifecycle state machine states (see 02_STATE_MACHINE.md).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[pyclass]
pub enum OrderState {
    IntentGenerated,
    RiskChecked,
    Rejected,           // Terminal
    WalWritten,
    Submitted,
    BrokerRejected,     // Terminal
    Acknowledged,
    Orphaned,           // No ack within 5s
    PartiallyFilled,
    Filled,
    ExitRegistered,
    ExitTriggered,
    ExitOrderSubmitted,
    ExitFilled,
    PositionClosed,     // Terminal
}

/// WAL event type discriminator.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum WalEventType {
    RoutedOrder,
    BrokerAck,
    FillEvent,
    ExitSignal,
    PositionClosed,
    RiskStateChange,
    OrphanResolved,
    StateSnapshot,
    SystemReady,
}
```

---

## CORE STRUCTS

### MarketTick
The atomic unit of market data. Crosses from Rust to Python in batches.

```rust
/// A single market data tick from IBKR.
/// Rust owns these. Python receives Vec<MarketTick> via PyO3.
/// Fields ordered largest-to-smallest for struct packing (H128).
#[derive(Clone, Debug)]
#[pyclass]
pub struct MarketTick {
    /// Unix epoch nanoseconds from IBKR server (H03). NOT wall clock.
    pub timestamp_ns: u64,
    /// Best bid price
    pub bid: f64,
    /// Best ask price
    pub ask: f64,
    /// Last traded price
    pub last: f64,
    /// Cumulative volume (resets daily)
    pub volume: u64,
    /// Interned ticker ID (H01). NOT a String.
    pub ticker_id: TickerId,
    /// Socket-level receive timestamp for T2T latency (H118)
    pub recv_timestamp_ns: u64,
}
```

**FFI Notes:**
- Python receives `list[MarketTick]`, NOT single ticks (H03, batch FFI)
- All f64 values are NaN-sanitized by Rust before crossing to Python (H09)
- Use `Option<f64>` instead of NaN for missing data (H10)
- Timestamps are u64 nanoseconds, Python converts to pd.Timestamp (H03)

---

### OrderIntent
Python's ONLY output. A suggestion, not an order.

```rust
/// Generated by Python Brain. Crosses PyO3 back to Rust.
/// Python SUGGESTS. Rust DECIDES. Python has no gun (Non-Negotiable #2).
#[derive(Clone, Debug)]
#[pyclass]
pub struct OrderIntent {
    /// Interned ticker ID
    pub ticker_id: TickerId,
    /// Long only in ISA (Short exists for type completeness but always rejected)
    pub side: Direction,
    /// Signal confidence [0.0, 100.0]. Floor is 65.
    pub confidence: f64,
    /// Which strategy generated this intent
    pub strategy: StrategyId,
    /// Kelly fraction output from 12-factor sizing [0.0, 0.20]
    pub kelly_fraction: f64,
    /// Strategy-specific features for logging/Ouroboros analysis
    /// e.g., {"adx": 28.5, "rvol": 2.3, "momentum_score": 0.85}
    pub features: std::collections::HashMap<String, f64>,
}
```

**Invariants:**
- `confidence` is clamped to [0.0, 100.0] — NaN check (H09)
- `kelly_fraction` is clamped to [0.0, 0.20] — Kelly clamp (H57)
- `side == Direction::Short` will ALWAYS be rejected by RiskArbiter (ISA)
- `features` is for observability only — Rust does not interpret it

---

### RiskDecision
The RiskArbiter's verdict on an OrderIntent.

```rust
/// Synchronous, fail-closed gate output.
/// Rust freezes state, evaluates ALL rules, returns verdict.
#[derive(Clone, Debug)]
#[pyclass]
pub struct RiskDecision {
    /// true = proceed to WAL + broker; false = rejected
    pub approved: bool,
    /// Adjusted position size (may be reduced in REDUCE regime)
    pub adjusted_size: f64,
    /// Specific reason for veto (or VetoReason::Approved)
    pub reason: VetoReason,
    /// Current Risk Arbiter regime at decision time
    pub regime: RiskRegime,
    /// Timestamp of decision (IBKR-adjusted clock)
    pub decision_timestamp_ns: u64,
}
```

---

### FillEvent
A fill (or partial fill) from the broker.

```rust
/// Received from IBKR via execDetails (H52, not orderStatus).
/// Multiple FillEvents per order are expected (partial fills, H124).
#[derive(Clone, Debug)]
pub struct FillEvent {
    /// Links to the RoutedOrder (UUIDv7, H22)
    pub order_id: OrderId,
    /// Ticker
    pub ticker_id: TickerId,
    /// Shares filled in THIS execution
    pub filled_qty: u32,
    /// Shares remaining after this fill
    pub remaining_qty: u32,
    /// Execution price (may have sub-penny precision, H115: 4 decimal places)
    pub price: f64,
    /// IBKR execution ID (unique per fill, used for deduplication)
    pub exec_id: String,
    /// IBKR server timestamp of fill
    pub timestamp_ns: u64,
    /// Commission for this fill (from commissionReport, H53)
    pub commission: f64,
}
```

**Invariants:**
- `price` supports 4 decimal places for dark pool midpoint fills (H115)
- VWAP entry = Σ(fill_price × fill_qty) / Σ(fill_qty)
- Stop-loss registers/updates per partial fill using actual filled_qty
- Position is CLOSED only when remaining_qty == 0 AND all shares exited

---

### PositionState
Tracks a live position through its lifecycle.

```rust
/// Maintained by the Executioner. Python receives a CLONE (H40).
/// Persisted in WAL via StateSnapshot events.
#[derive(Clone, Debug)]
#[pyclass]
pub struct PositionState {
    /// Ticker
    pub ticker_id: TickerId,
    /// Current position quantity (shares held)
    pub qty: u32,
    /// Volume-weighted average entry price (FIFO, H87)
    pub avg_entry: f64,
    /// Unrealized PnL based on last tick
    pub unrealized_pnl: f64,
    /// Realized PnL (from partial exits)
    pub realized_pnl: f64,
    /// Highest price since entry (for Chandelier trailing stop, H70)
    /// MUST survive crash recovery (persisted in WAL)
    pub highest_high: f64,
    /// Current stop-loss price (ratchets UP only, H68)
    pub stop_price: f64,
    /// Current Chandelier rung (0-5, 0 = no rung reached)
    pub trailing_rung: u8,
    /// Entry timestamp for time-based exit calculations
    pub entry_timestamp_ns: u64,
    /// Total commission paid (entry + partial exits)
    pub total_commission: f64,
    /// Order lifecycle state
    pub state: OrderState,
    /// The WAL OrderId that created this position
    pub origin_order_id: OrderId,
}
```

**Invariants:**
- `highest_high` is ALWAYS >= `avg_entry` (set on first fill, ratchets up)
- `stop_price` can NEVER decrease (new_stop = max(old_stop, calculated_stop), H68)
- `trailing_rung` maps to Chandelier 5-rung profit ladder (see Exit Engine)
- Python receives `PositionState.clone()` — NEVER a reference (H40)

---

### BrokerAck
Confirmation (or rejection) from IBKR for a submitted order.

```rust
/// Received after submitting a RoutedOrder to IBKR.
/// Timeout: 5 seconds. No ack → ORPHANED state.
#[derive(Clone, Debug)]
pub struct BrokerAck {
    /// Links to the RoutedOrder
    pub order_id: OrderId,
    /// IBKR's response
    pub status: BrokerAckStatus,
    /// IBKR's internal order ID (for reqOpenOrders reconciliation)
    pub ibkr_order_id: i64,
    /// IBKR server timestamp
    pub timestamp_ns: u64,
    /// Human-readable reason (if rejected)
    pub message: Option<String>,
}
```

---

### ExitSignal
Generated by the Exit Engine when an exit condition fires.

```rust
/// The Exit Engine evaluates ALL exit conditions on EVERY tick for
/// EVERY open position. If multiple fire → highest priority wins.
#[derive(Clone, Debug)]
pub struct ExitSignal {
    /// Which position to exit
    pub ticker_id: TickerId,
    /// Why we're exiting
    pub reason: ExitReason,
    /// Priority for collision resolution (higher wins)
    pub priority: ExitPriority,
    /// How to execute the exit
    pub order_type: ExitOrderType,
    /// The OrderId of the position being exited
    pub position_order_id: OrderId,
    /// Desired exit price (for limit orders) or 0.0 for market
    pub limit_price: Option<f64>,
}
```

---

## WAL EVENT ENVELOPE

Every event written to the ndjson journal is wrapped in this envelope:

```rust
/// Universal WAL event wrapper.
/// Every line in events/YYYY-MM-DD.ndjson is a serialized WalEvent.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct WalEvent {
    /// UUIDv7, time-ordered (H22)
    pub event_id: String,
    /// Schema version for forward compatibility (H21)
    pub schema_version: u8,
    /// When the business logic fired (IBKR clock)
    pub event_time_ns: u64,
    /// When this line hit disk (system clock)
    pub write_time_ns: u64,
    /// CRC32 checksum of the payload JSON (H24)
    pub checksum: u32,
    /// The actual event payload
    pub payload: WalPayload,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum WalPayload {
    RoutedOrder { /* serialized OrderIntent + RiskDecision */ },
    BrokerAck { /* serialized BrokerAck */ },
    FillEvent { /* serialized FillEvent */ },
    ExitSignal { /* serialized ExitSignal */ },
    PositionClosed { ticker_id: u32, final_pnl: f64, entry_time_ns: u64, exit_time_ns: u64 },
    RiskStateChange { from: String, to: String, trigger: String },
    OrphanResolved { order_id: String, resolution: String },
    StateSnapshot { portfolio_json: String, equity: f64, high_water: f64, hash: String },
    SystemReady { wal_events_replayed: u64, positions_reconciled: u32 },
}
```

---

## FFI BOUNDARY RULES (SUMMARY)

| Rule | Enforcement |
|------|-------------|
| No String ticker comparisons in hot path (H01) | TickerId(u32) newtype |
| Timestamps as u64 nanoseconds (H03) | u64, not chrono::DateTime |
| Enums not strings across FFI (H04) | Direction, StrategyId enums |
| Python exceptions → Rust BrainError (H05) | Mapped via PyO3 |
| NaN sanitization on every f64 from Python (H09) | val.is_nan() check |
| Option<f64> not NaN for missing data (H10) | Rust type system |
| Pre-allocated buffers (H02) | Vec::with_capacity(10_000) |
| Struct packing largest-to-smallest (H128) | Manual field ordering |
| Static PyString interning (H127) | Boot-time allocation |
| Immutable clones to Python (H40) | .clone() before crossing |

---

## TYPE COUNT VERIFICATION

- Core structs: 7 (MarketTick, OrderIntent, RiskDecision, FillEvent, PositionState, BrokerAck, ExitSignal)
- Newtypes: 2 (TickerId, OrderId)
- Enums: 10 (Direction, StrategyId, VetoReason, RiskRegime, BrokerAckStatus, ExitReason, ExitPriority, ExitOrderType, OrderState, WalEventType)
- WAL types: 2 (WalEvent, WalPayload)
- Total defined types: 21
