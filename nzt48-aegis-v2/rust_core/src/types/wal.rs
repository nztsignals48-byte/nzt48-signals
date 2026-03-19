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
        /// Human-readable symbol (e.g. "QQQ3.L"). Added in schema_version=1.
        #[serde(default)]
        symbol: String,
        /// Share count. Added in schema_version=1.
        #[serde(default)]
        qty: u32,
        /// Native currency (e.g. "GBP", "USD"). Added in schema_version=1.
        #[serde(default)]
        currency: String,
        /// RVOL at entry (Phase H: indicator context for Ouroboros learning).
        #[serde(default)]
        entry_rvol: f64,
        /// Hurst exponent at entry (Phase H: indicator context for Ouroboros learning).
        #[serde(default)]
        entry_hurst: f64,
        /// ADX at entry (Phase H: indicator context for Ouroboros learning).
        #[serde(default)]
        entry_adx: f64,
        /// Bid-ask spread at entry in percent. For execution cost analysis.
        #[serde(default)]
        spread_pct: f64,
        /// Price distance from VWAP at entry in percent. For extension detection.
        #[serde(default)]
        vwap_distance_pct: f64,
        /// Volume trend slope at entry. Positive = rising volume = real flow.
        #[serde(default)]
        volume_slope: f64,
        /// Leverage factor of the instrument (3, 5, etc). For leverage-aware analysis.
        #[serde(default)]
        leverage: u8,
        /// Trading session mode at entry (e.g. "ModeB", "ModeBPlus"). For session analysis.
        #[serde(default)]
        session_mode: String,
        /// Entry price in native currency.
        #[serde(default)]
        entry_price: f64,
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
        /// Human-readable symbol (e.g. "QQQ3.L"). Added in schema_version=1.
        #[serde(default)]
        symbol: String,
        /// Share count closed. Added in schema_version=1.
        #[serde(default)]
        qty: u32,
        /// Risk regime at time of entry (e.g. "Normal", "Reduce"). For Ouroboros learning.
        #[serde(default)]
        regime_at_entry: String,
        /// Confidence score at entry. For Ouroboros learning.
        #[serde(default)]
        confidence: f64,
        /// Highest chandelier rung reached (1-5). For exit ladder calibration.
        #[serde(default)]
        highest_rung: u8,
        /// Strategy name (e.g. "VanguardSniper"). For per-strategy analysis.
        #[serde(default)]
        strategy: String,
        /// Exchange MIC (e.g. "XLON"). For per-exchange analysis.
        #[serde(default)]
        exchange: String,
        /// Entry price in GBP. For Ouroboros MAE/MFE analysis.
        #[serde(default)]
        entry_price: f64,
        /// Exit price in GBP. For Ouroboros MAE/MFE analysis.
        #[serde(default)]
        exit_price: f64,
        /// RVOL at entry (Phase H: indicator context for Ouroboros learning).
        #[serde(default)]
        entry_rvol: f64,
        /// Hurst exponent at entry (Phase H: indicator context for Ouroboros learning).
        #[serde(default)]
        entry_hurst: f64,
        /// ADX at entry (Phase H: indicator context for Ouroboros learning).
        #[serde(default)]
        entry_adx: f64,
        /// Spread at entry in percent.
        #[serde(default)]
        entry_spread_pct: f64,
        /// Spread at exit in percent.
        #[serde(default)]
        exit_spread_pct: f64,
        /// VWAP distance at entry in percent.
        #[serde(default)]
        entry_vwap_pct: f64,
        /// Volume slope at entry (positive = rising).
        #[serde(default)]
        entry_vol_slope: f64,
        /// Leverage factor (3, 5, etc).
        #[serde(default)]
        leverage: u8,
        /// Session mode at entry (ModeA, ModeB, ModeBPlus, ModeC).
        #[serde(default)]
        session_mode: String,
        /// Hold duration in seconds (convenience field, also computable from timestamps).
        #[serde(default)]
        hold_secs: u64,
        /// Exit reason (Chandelier, EOD, Halt, Signal, TimeStop).
        #[serde(default)]
        exit_reason: String,
        /// Maximum Adverse Excursion: worst unrealized P&L during position lifetime.
        #[serde(default)]
        mae: f64,
        /// Maximum Favorable Excursion: best unrealized P&L during position lifetime.
        #[serde(default)]
        mfe: f64,
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
        /// Per-position snapshot: JSON array of {symbol, qty, entry_price, current_price,
        /// unrealized_pnl, rung, stop_price, highest_high, exchange}
        /// Added 2026-03-18 for Google Sheets Open_Positions tab + per-ticker unrealised P&L
        #[serde(default)]
        open_positions: Vec<serde_json::Value>,
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
    /// Chandelier rung advance — persists rung state across restarts.
    /// Written when trailing_rung increases for an open position.
    RungAdvanced {
        ticker_id: u32,
        order_id: String,
        old_rung: u8,
        new_rung: u8,
        stop_price: f64,
        highest_high: f64,
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
