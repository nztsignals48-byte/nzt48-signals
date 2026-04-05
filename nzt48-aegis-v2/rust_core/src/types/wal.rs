//! Write-Ahead Log (WAL) types for crash recovery and event sourcing.
//! Every line in events/YYYY-MM-DD.ndjson is a serialized WalEvent.

use serde::{Deserialize, Serialize};

use super::enums::WalEventType;

// ============================================================================
// WAL TYPES
// ============================================================================

fn default_wal_version() -> String {
    "1.1".to_string()
}

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
    /// WAL format version for forward compatibility (P1-2.9).
    #[serde(default = "default_wal_version")]
    pub wal_version: String,
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
        /// RSI at entry (P1-2.8: full indicator snapshot for Ouroboros learning).
        #[serde(default)]
        rsi: f64,
        /// VWAP distance % at entry (P1-2.8: full indicator snapshot for Ouroboros learning).
        #[serde(default)]
        vwap_dist_pct: f64,
        /// ATR at entry (P1-2.8: full indicator snapshot for Ouroboros learning).
        #[serde(default)]
        atr: f64,
        /// Volume slope at entry (P1-2.8: full indicator snapshot for Ouroboros learning).
        #[serde(default)]
        vol_slope: f64,
        /// Bid-ask spread % at entry (P1-2.8: full indicator snapshot for Ouroboros learning).
        #[serde(default)]
        spread_pct: f64,
        /// Multi-timeframe confirmation score at entry (P1-2.8: full indicator snapshot for Ouroboros learning).
        #[serde(default)]
        mtf_score: f64,
        /// TypeA-F entry classification from bridge.py classify_entry_type().
        #[serde(default)]
        entry_type: String,
        /// IBS (Internal Bar Strength) at entry.
        #[serde(default)]
        ibs: f64,
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
        /// N0f: Bid-ask spread % at moment of fill. For cost attribution.
        #[serde(default)]
        spread_at_fill_pct: f64,
        /// N0f: Buy or Sell side. For cost attribution.
        #[serde(default)]
        side: String,
    },
    ExitSignal {
        ticker_id: u32,
        reason: String,
        priority: String,
    },
    PositionClosed {
        ticker_id: u32,
        /// Net PnL after commission (existing field, renamed semantically).
        final_pnl: f64,
        entry_time_ns: u64,
        exit_time_ns: u64,
        /// N0e: Gross PnL before commission. gross_pnl - commission = final_pnl.
        #[serde(default)]
        gross_pnl: f64,
        /// N0e: Total commission (entry + exit).
        #[serde(default)]
        total_commission: f64,
        /// N0e: Bid-ask spread % at entry. For L5 Spread Victim detection.
        #[serde(default)]
        spread_at_entry_pct: f64,
        /// N0e: Bid-ask spread % at exit. For cost attribution.
        #[serde(default)]
        spread_at_exit_pct: f64,
        /// N0e: Daily trade number (1st, 2nd, 3rd of the day). For frequency analysis.
        #[serde(default)]
        daily_trade_number: u32,
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
        /// Strategy name (e.g. "TypeB", "TypeF"). For per-strategy analysis.
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
        /// Maximum Adverse Excursion: worst unrealized P&L during position lifetime.
        #[serde(default)]
        mae: f64,
        /// Maximum Favorable Excursion: best unrealized P&L during position lifetime.
        #[serde(default)]
        mfe: f64,
        /// N2b: Hold time in minutes (exit_time - entry_time). For trade taxonomy classification.
        #[serde(default)]
        hold_time_mins: u32,
        /// N2b: Market session phase at entry ("open", "morning", "afternoon", "close").
        /// For late-entry detection in trade taxonomy.
        #[serde(default)]
        entry_session_phase: String,
        /// N2b: VWAP distance at entry (%). For overextension detection.
        #[serde(default)]
        vwap_dist_at_entry_pct: f64,
        /// N2b: ATR % at entry. For stop-hunt detection in trade taxonomy.
        #[serde(default)]
        atr_pct_at_entry: f64,
        /// N2b: VIX level at entry. For regime-conditioned analysis.
        #[serde(default)]
        vix_at_entry: f64,
        /// N2b: Volume slope at entry. For momentum quality assessment.
        #[serde(default)]
        vol_slope_at_entry: f64,
        /// N2b: Trade taxonomy class assigned by nightly analysis.
        /// One of: clean_trend, grind_winner, spike_winner, lucky_winner,
        /// breakeven_win, spread_victim, noise_exit, stop_hunt,
        /// thesis_failure, late_entry, overextension, gap_against,
        /// flash_crash, corr_break.
        #[serde(default)]
        trade_class: String,
        /// TypeA-F entry classification (from bridge.py at entry time).
        #[serde(default)]
        entry_type: String,
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
    // Dead variants removed: NextValidId, QuoteImbalanceInvalidated, SplitAdjustment
    // — defined but never written by any code path. Match arms in wal_replay.rs also removed.
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
    /// N2a: Signal was generated by Python but rejected by a gate.
    /// Enables missed-winner analysis: was the rejected signal actually right?
    /// BUILD NOW item N2a from IMPLEMENTATION_MASTER_PLAN v6.0.
    SignalRejected {
        ticker_id: u32,
        symbol: String,
        strategy: String,
        confidence: f64,
        gate_name: String,
        gate_reason: String,
        /// Indicator snapshot at rejection time for counterfactual analysis.
        #[serde(default)]
        hurst: f64,
        #[serde(default)]
        adx: f64,
        #[serde(default)]
        rvol: f64,
        #[serde(default)]
        vol_slope: f64,
        #[serde(default)]
        spread_pct: f64,
        #[serde(default)]
        price_at_reject: f64,
    },
    // Dead variant removed: MissedWinnerCandidate — defined but never written.
    // N2c analysis is done entirely in Python nightly; Rust never emits this event.
    /// P0-1.4: Kelly ramp counter persistence across restarts.
    /// Written on each simulated/live fill. WAL replay restores highest count.
    KellyRampAdvance {
        count: u64,
    },
    /// P2-3.6: Every generated signal (including approved ones) for full funnel analysis.
    SignalGenerated {
        ticker_id: u32,
        symbol: String,
        strategy: String,
        confidence: f64,
        direction: String,
        /// Whether this signal was approved by risk arbiter.
        #[serde(default)]
        approved: bool,
        /// Veto reason if rejected (empty if approved).
        #[serde(default)]
        veto_reason: String,
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
            wal_version: "1.1".to_string(),
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
