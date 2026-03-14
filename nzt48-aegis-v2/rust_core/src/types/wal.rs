//! Write-Ahead Log (WAL) types for crash recovery and event sourcing.
//! Every line in events/YYYY-MM-DD.ndjson is a serialized WalEvent.

use serde::{Deserialize, Serialize};

use super::enums::WalEventType;

// ============================================================================
// WAL TYPES
// ============================================================================

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
    RoutedOrder {
        order_id: String,
        ticker_id: u32,
        side: String,
        confidence: f64,
        strategy: String,
        kelly_fraction: f64,
        approved_size: f64,
    },
    BrokerAck {
        order_id: String,
        status: String,
        ibkr_order_id: i64,
    },
    FillEvent {
        order_id: String,
        ticker_id: u32,
        filled_qty: u32,
        remaining_qty: u32,
        price: f64,
        exec_id: String,
        commission: f64,
    },
    ExitSignal {
        ticker_id: u32,
        reason: String,
        priority: String,
    },
    PositionClosed {
        ticker_id: u32,
        final_pnl: f64,
        entry_time_ns: u64,
        exit_time_ns: u64,
    },
    RiskStateChange {
        from: String,
        to: String,
        trigger: String,
    },
    OrphanResolved {
        order_id: String,
        resolution: String,
    },
    StateSnapshot {
        portfolio_json: String,
        equity: f64,
        high_water: f64,
        hash: String,
    },
    SystemReady {
        wal_events_replayed: u64,
        positions_reconciled: u32,
    },
    NextValidId {
        id: u64,
    },
    /// SC-17: Quote imbalance signal suspension.
    QuoteImbalanceInvalidated {
        ticker_id: u32,
        dropped_count: u32,
        resumed_at_ts: u64,
    },
    /// SC-13: Split adjustment event for Kelly ramp recalibration.
    SplitAdjustment {
        ticker_id: u32,
        ratio_numerator: u32,
        ratio_denominator: u32,
    },
    /// SC-01: System shutting down gracefully.
    SystemShutdown {
        positions_flattened: u32,
        pending_fills_waited_secs: u32,
    },
    /// P2-A: Reconciliation divergence detected.
    ReconciliationDivergence {
        mismatches: Vec<String>,
        timestamp_ns: u64,
    },
    /// P2-A: Reconciliation divergence cleared (manual or auto).
    ReconciliationCleared {
        cleared_by: String,
        timestamp_ns: u64,
    },
    /// P2-C: Daily reset event for crash recovery.
    DailyReset {
        date: String,
        previous_equity: f64,
        new_equity: f64,
    },
}

// Suppress unused warning — WalEventType is part of the data contract
// and will be used when WAL write/read logic is implemented in Phase 3.
const _: () = {
    fn _assert_wal_event_type_exists(_: WalEventType) {}
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_wal_event_serialization() {
        let event = WalEvent {
            event_id: "test-uuid".to_string(),
            schema_version: 1,
            event_time_ns: 1_000_000_000,
            write_time_ns: 1_000_000_100,
            checksum: 0,
            payload: WalPayload::SystemReady {
                wal_events_replayed: 42,
                positions_reconciled: 2,
            },
        };
        let json = serde_json::to_string(&event);
        assert!(json.is_ok());
        let json_str = json.expect("serialization succeeded in test");
        assert!(json_str.contains("SystemReady"));
        assert!(json_str.contains("\"schema_version\":1"));
    }
}
