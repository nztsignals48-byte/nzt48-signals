//! Execution-phase #[pyclass] structs: FillEvent, PositionState, BrokerAck, ExitSignal.
//! Fields ordered largest-to-smallest for struct packing (H128).

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

use super::enums::*;

// ============================================================================
// EXECUTION STRUCTS
// ============================================================================

/// A fill (or partial fill) from the broker.
/// Received from IBKR via execDetails (H52, not orderStatus).
#[derive(Clone, Debug, Serialize, Deserialize)]
#[pyclass]
pub struct FillEvent {
    /// IBKR server timestamp of fill
    #[pyo3(get)]
    pub timestamp_ns: u64,
    /// Execution price (may have sub-penny precision, H115: 4 decimal places)
    #[pyo3(get)]
    pub price: f64,
    /// Commission for this fill (from commissionReport, H53)
    #[pyo3(get)]
    pub commission: f64,
    /// Shares filled in THIS execution
    #[pyo3(get)]
    pub filled_qty: u32,
    /// Shares remaining after this fill
    #[pyo3(get)]
    pub remaining_qty: u32,
    /// Interned ticker ID
    #[pyo3(get)]
    pub ticker_id: TickerId,
    /// Links to the RoutedOrder (UUIDv7, H22)
    #[pyo3(get)]
    pub order_id: String,
    /// IBKR execution ID (unique per fill, used for deduplication)
    #[pyo3(get)]
    pub exec_id: String,
}

#[pymethods]
impl FillEvent {
    #[new]
    #[pyo3(signature = (order_id, ticker_id, filled_qty, remaining_qty, price, exec_id, timestamp_ns, commission))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        order_id: String,
        ticker_id: TickerId,
        filled_qty: u32,
        remaining_qty: u32,
        price: f64,
        exec_id: String,
        timestamp_ns: u64,
        commission: f64,
    ) -> Self {
        Self {
            timestamp_ns,
            price,
            commission,
            filled_qty,
            remaining_qty,
            ticker_id,
            order_id,
            exec_id,
        }
    }
}

/// Tracks a live position through its lifecycle.
/// Maintained by the Executioner. Python receives a CLONE (H40).
#[derive(Clone, Debug)]
#[pyclass]
pub struct PositionState {
    /// Entry timestamp for time-based exit calculations
    #[pyo3(get)]
    pub entry_timestamp_ns: u64,
    /// Volume-weighted average entry price (FIFO, H87)
    #[pyo3(get)]
    pub avg_entry: f64,
    /// Unrealized PnL based on last tick
    #[pyo3(get)]
    pub unrealized_pnl: f64,
    /// Realized PnL (from partial exits)
    #[pyo3(get)]
    pub realized_pnl: f64,
    /// Highest price since entry (for Chandelier trailing stop, H70)
    /// MUST survive crash recovery (persisted in WAL)
    #[pyo3(get)]
    pub highest_high: f64,
    /// Current stop-loss price (ratchets UP only, H68)
    #[pyo3(get)]
    pub stop_price: f64,
    /// Total commission paid (entry + partial exits)
    #[pyo3(get)]
    pub total_commission: f64,
    /// Current position quantity (shares held)
    #[pyo3(get)]
    pub qty: u32,
    /// Interned ticker ID
    #[pyo3(get)]
    pub ticker_id: TickerId,
    /// Current Chandelier rung (0-5, 0 = no rung reached)
    #[pyo3(get)]
    pub trailing_rung: u8,
    /// Order lifecycle state
    #[pyo3(get)]
    pub state: OrderState,
    /// The WAL OrderId that created this position (stored as String for PyO3)
    #[pyo3(get)]
    pub origin_order_id: String,
    /// P21: Whether position is in carry state (stops frozen, no Chandelier evaluation)
    #[pyo3(get)]
    pub is_carried: bool,
    /// Maximum Adverse Excursion: worst unrealized P&L (most negative) during position lifetime.
    /// Used for stop optimization — how far against us did the trade go?
    #[pyo3(get)]
    pub mae: f64,
    /// Maximum Favorable Excursion: best unrealized P&L (most positive) during position lifetime.
    /// Used for target optimization — how much profit was available before exit?
    #[pyo3(get)]
    pub mfe: f64,
    /// N0e: Bid-ask spread % at entry. Captured at fill time for cost attribution.
    #[pyo3(get)]
    pub spread_at_entry_pct: f64,
    /// N0a: Which trade number this was in the day (1st, 2nd, 3rd...).
    #[pyo3(get)]
    pub daily_trade_number: u32,
    /// TypeA-F entry classification from bridge.py.
    #[pyo3(get)]
    pub entry_type: String,
    /// S3: Active trading ticks since entry. Incremented by update_tracking() each tick.
    /// Used for time-stop: active_trading_ticks / 12 ≈ active minutes (at ~5s tick interval).
    /// Unlike wall-clock time, this pauses during exchange halts (no ticks = no increment).
    #[pyo3(get)]
    pub active_trading_ticks: u32,
    // ── EXIT HINT FIELDS (from BrainSignal, consumed by exit_engine) ──
    /// Max holding period in hours (time-stop). None = no time limit.
    pub max_hold_hours: Option<f64>,
    /// Hours after which to start tightening stops (urgency ramp).
    pub exit_urgency_ramp_hours: Option<f64>,
    /// Per-signal initial Chandelier stop ATR multiplier.
    pub suggested_initial_stop_atr_mult: Option<f64>,
    /// Per-signal Rung 3 trailing ATR multiplier.
    pub suggested_rung3_atr: Option<f64>,
    /// Minimum profit target % (don't advance to breakeven below this).
    pub min_profit_target_pct: Option<f64>,
    /// Exit trail bias: "wide" (trending, let run), "tight" (MR, capture), "neutral".
    /// Consumed by Chandelier to adjust rung3/4/5 multipliers.
    pub exit_trail_bias: Option<String>,
    /// Number of partial exits completed (0, 1, 2 max).
    pub partial_exits_done: u8,
}

#[pymethods]
impl PositionState {
    #[new]
    #[pyo3(signature = (ticker_id, qty, avg_entry, stop_price, entry_timestamp_ns, origin_order_id))]
    fn new(
        ticker_id: TickerId,
        qty: u32,
        avg_entry: f64,
        stop_price: f64,
        entry_timestamp_ns: u64,
        origin_order_id: String,
    ) -> Self {
        Self {
            entry_timestamp_ns,
            avg_entry,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            highest_high: avg_entry,
            stop_price,
            total_commission: 0.0,
            qty,
            ticker_id,
            trailing_rung: 0,
            state: OrderState::Filled,
            origin_order_id,
            is_carried: false,
            mae: 0.0,
            mfe: 0.0,
            spread_at_entry_pct: 0.0,
            daily_trade_number: 0,
            entry_type: String::new(),
            active_trading_ticks: 0,
            max_hold_hours: None,
            exit_urgency_ramp_hours: None,
            suggested_initial_stop_atr_mult: None,
            suggested_rung3_atr: None,
            min_profit_target_pct: None,
            exit_trail_bias: None,
            partial_exits_done: 0,
        }
    }
}

/// Confirmation (or rejection) from IBKR for a submitted order.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[pyclass]
pub struct BrokerAck {
    /// IBKR server timestamp
    #[pyo3(get)]
    pub timestamp_ns: u64,
    /// IBKR's internal order ID (for reqOpenOrders reconciliation)
    #[pyo3(get)]
    pub ibkr_order_id: i64,
    /// Links to the RoutedOrder
    #[pyo3(get)]
    pub order_id: String,
    /// IBKR's response
    #[pyo3(get)]
    pub status: BrokerAckStatus,
    /// Human-readable reason (if rejected)
    #[pyo3(get)]
    pub message: Option<String>,
}

#[pymethods]
impl BrokerAck {
    #[new]
    #[pyo3(signature = (order_id, status, ibkr_order_id, timestamp_ns, message=None))]
    fn new(
        order_id: String,
        status: BrokerAckStatus,
        ibkr_order_id: i64,
        timestamp_ns: u64,
        message: Option<String>,
    ) -> Self {
        Self {
            timestamp_ns,
            ibkr_order_id,
            order_id,
            status,
            message,
        }
    }
}

/// Generated by the Exit Engine when an exit condition fires.
#[derive(Clone, Debug)]
#[pyclass]
pub struct ExitSignal {
    /// Desired exit price (for limit orders) or None for market
    #[pyo3(get)]
    pub limit_price: Option<f64>,
    /// Which position to exit
    #[pyo3(get)]
    pub ticker_id: TickerId,
    /// Why we're exiting
    #[pyo3(get)]
    pub reason: ExitReason,
    /// Priority for collision resolution (higher wins)
    #[pyo3(get)]
    pub priority: ExitPriority,
    /// How to execute the exit
    #[pyo3(get)]
    pub order_type: ExitOrderType,
    /// The OrderId of the position being exited (as String for PyO3)
    #[pyo3(get)]
    pub position_order_id: String,
    /// Partial exit: sell only this many shares (None = sell all).
    /// Used for profit laddering: 25% at Rung 3, 25% at Rung 4, 50% trails.
    #[pyo3(get)]
    pub partial_qty: Option<u32>,
}

#[pymethods]
impl ExitSignal {
    #[new]
    #[pyo3(signature = (ticker_id, reason, priority, order_type, position_order_id, limit_price=None))]
    fn new(
        ticker_id: TickerId,
        reason: ExitReason,
        priority: ExitPriority,
        order_type: ExitOrderType,
        position_order_id: String,
        limit_price: Option<f64>,
    ) -> Self {
        Self {
            limit_price,
            ticker_id,
            reason,
            priority,
            order_type,
            position_order_id,
            partial_qty: None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_position_state_defaults() {
        let pos = PositionState {
            entry_timestamp_ns: 1_000_000_000,
            avg_entry: 10.50,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            highest_high: 10.50,
            stop_price: 10.00,
            total_commission: 0.0,
            qty: 100,
            ticker_id: TickerId(42),
            trailing_rung: 0,
            state: OrderState::Filled,
            origin_order_id: "test-id".to_string(),
            is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: String::new(),
                active_trading_ticks: 0,
        };
        assert!(pos.highest_high >= pos.avg_entry);
        assert_eq!(pos.trailing_rung, 0);
    }

    #[test]
    fn test_fill_event_fields() {
        let fill = FillEvent {
            timestamp_ns: 1_000_000_000,
            price: 10.5001,
            commission: 1.50,
            filled_qty: 37,
            remaining_qty: 63,
            ticker_id: TickerId(42),
            order_id: "test-uuid".to_string(),
            exec_id: "exec-001".to_string(),
        };
        assert_eq!(fill.filled_qty, 37);
        assert_eq!(fill.remaining_qty, 63);
        assert!((fill.price - 10.5001).abs() < 1e-10);
    }
}
