//! Position reconciliation: compare broker-reported state vs local portfolio.
//! Runs every 5 minutes (H130). Any mismatch → CRITICAL log + FLATTEN.
//! Orphan detection: broker orders not in local state → reconcile before NORMAL.

use crate::broker::{BrokerOpenOrder, BrokerPosition};
use crate::portfolio::PortfolioState;
use crate::types::TickerId;

/// A single position mismatch between local and broker state.
#[derive(Debug, Clone)]
pub enum PositionMismatch {
    QuantityDiff {
        ticker_id: TickerId,
        local_qty: u32,
        broker_qty: u32,
    },
    CostDiff {
        ticker_id: TickerId,
        local_avg: f64,
        broker_avg: f64,
    },
    /// Broker has a position we don't know about.
    BrokerOnly {
        ticker_id: TickerId,
        broker_qty: u32,
    },
    /// We have a position the broker doesn't report.
    LocalOnly { ticker_id: TickerId, local_qty: u32 },
}

/// P1-2.13: Display trait for structured mismatch logging.
impl std::fmt::Display for PositionMismatch {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::QuantityDiff { ticker_id, local_qty, broker_qty } => {
                write!(f, "QtyDrift ticker={} local={} broker={}", ticker_id.0, local_qty, broker_qty)
            }
            Self::CostDiff { ticker_id, local_avg, broker_avg } => {
                write!(f, "CostDiff ticker={} local={:.4} broker={:.4}", ticker_id.0, local_avg, broker_avg)
            }
            Self::BrokerOnly { ticker_id, broker_qty } => {
                write!(f, "BrokerOnly ticker={} qty={}", ticker_id.0, broker_qty)
            }
            Self::LocalOnly { ticker_id, local_qty } => {
                write!(f, "LocalOnly ticker={} qty={}", ticker_id.0, local_qty)
            }
        }
    }
}

/// Result of a position reconciliation check.
#[derive(Debug)]
pub struct ReconcileResult {
    /// Number of positions that match perfectly.
    pub matches: usize,
    /// All detected mismatches.
    pub mismatches: Vec<PositionMismatch>,
    /// Order IDs in broker but not tracked locally (orphans).
    pub orphaned_orders: Vec<String>,
    /// Whether the reconciliation is clean (no mismatches or orphans).
    pub is_clean: bool,
}

/// WP-2: Audit log for reconciliation mismatches.
/// Tracks mismatches with timestamps, enforces a 24h lock period,
/// and prevents automatic regime reset (requires manual_clear_halt).
#[derive(Debug)]
pub struct ReconcileAuditLog {
    /// Recorded mismatch entries with nanosecond timestamps.
    pub entries: Vec<AuditEntry>,
    /// Nanosecond timestamp when halt was triggered (0 = no halt).
    pub halt_triggered_ns: u64,
    /// Whether a manual clear has been issued.
    pub manually_cleared: bool,
}

/// A single audit log entry recording a reconciliation mismatch.
#[derive(Debug, Clone)]
pub struct AuditEntry {
    pub timestamp_ns: u64,
    pub mismatch: PositionMismatch,
}

/// 24 hours in nanoseconds.
const LOCK_PERIOD_NS: u64 = 24 * 3600 * 1_000_000_000;

impl ReconcileAuditLog {
    pub fn new() -> Self {
        Self {
            entries: Vec::new(),
            halt_triggered_ns: 0,
            manually_cleared: false,
        }
    }

    /// Record a mismatch. Triggers halt lock if not already halted.
    pub fn record(&mut self, mismatch: PositionMismatch, now_ns: u64) {
        self.entries.push(AuditEntry {
            timestamp_ns: now_ns,
            mismatch,
        });
        if self.halt_triggered_ns == 0 {
            self.halt_triggered_ns = now_ns;
            self.manually_cleared = false;
        }
    }

    /// Whether the engine is currently locked (halt active, not manually cleared,
    /// and within the 24h lock period).
    pub fn is_locked(&self, now_ns: u64) -> bool {
        if self.halt_triggered_ns == 0 || self.manually_cleared {
            return false;
        }
        now_ns < self.halt_triggered_ns + LOCK_PERIOD_NS
    }

    /// Manual clear — the ONLY way to resume before the 24h lock expires.
    pub fn manual_clear_halt(&mut self) {
        self.manually_cleared = true;
    }

    /// Number of recorded mismatches.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
}

impl Default for ReconcileAuditLog {
    fn default() -> Self {
        Self::new()
    }
}

/// Compare local portfolio positions against broker-reported positions.
/// Returns detailed mismatch report.
pub fn reconcile_positions(
    portfolio: &PortfolioState,
    broker_positions: &[BrokerPosition],
) -> ReconcileResult {
    let mut matches = 0usize;
    let mut mismatches = Vec::new();
    let mut seen_tickers = std::collections::HashSet::new();

    // Check each broker position against local state
    for bp in broker_positions {
        seen_tickers.insert(bp.ticker_id);
        match portfolio.get_position(&bp.ticker_id) {
            Some(local_pos) => {
                if local_pos.qty != bp.qty {
                    mismatches.push(PositionMismatch::QuantityDiff {
                        ticker_id: bp.ticker_id,
                        local_qty: local_pos.qty,
                        broker_qty: bp.qty,
                    });
                // £0.01 tolerance appropriate for LSE ETPs (£20+ prices)
                } else if (local_pos.avg_entry - bp.avg_cost).abs() > 0.01 {
                    mismatches.push(PositionMismatch::CostDiff {
                        ticker_id: bp.ticker_id,
                        local_avg: local_pos.avg_entry,
                        broker_avg: bp.avg_cost,
                    });
                } else {
                    matches += 1;
                }
            }
            None => {
                mismatches.push(PositionMismatch::BrokerOnly {
                    ticker_id: bp.ticker_id,
                    broker_qty: bp.qty,
                });
            }
        }
    }

    // Check for local positions not in broker
    for tid in portfolio.positions().keys() {
        if !seen_tickers.contains(tid)
            && let Some(pos) = portfolio.get_position(tid)
        {
            mismatches.push(PositionMismatch::LocalOnly {
                ticker_id: *tid,
                local_qty: pos.qty,
            });
        }
    }

    let is_clean = mismatches.is_empty();
    ReconcileResult {
        matches,
        mismatches,
        orphaned_orders: Vec::new(),
        is_clean,
    }
}

/// Detect orphaned orders: broker-reported open orders not tracked locally.
/// `known_order_ids` are the order IDs we've submitted and are tracking.
pub fn detect_orphaned_orders(
    known_order_ids: &[String],
    broker_orders: &[BrokerOpenOrder],
) -> Vec<String> {
    let known: std::collections::HashSet<&str> =
        known_order_ids.iter().map(|s| s.as_str()).collect();
    broker_orders
        .iter()
        .filter(|bo| !known.contains(bo.order_id.as_str()))
        .map(|bo| bo.order_id.clone())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{OrderState, PositionState};

    fn make_position(tid: u32, qty: u32, avg_entry: f64) -> PositionState {
        PositionState {
            entry_timestamp_ns: 1_000_000,
            avg_entry,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            highest_high: avg_entry,
            stop_price: avg_entry * 0.95,
            total_commission: 1.50,
            qty,
            ticker_id: TickerId(tid),
            trailing_rung: 0,
            state: OrderState::Filled,
            origin_order_id: format!("order-{tid}"),
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

    #[test]
    fn test_perfect_match() {
        let mut portfolio = PortfolioState::new(10_000.0);
        portfolio.add_position(make_position(1, 100, 10.0));
        portfolio.add_position(make_position(2, 50, 20.0));

        let broker = vec![
            BrokerPosition {
                ticker_id: TickerId(1),
                qty: 100,
                avg_cost: 10.0,
            },
            BrokerPosition {
                ticker_id: TickerId(2),
                qty: 50,
                avg_cost: 20.0,
            },
        ];

        let result = reconcile_positions(&portfolio, &broker);
        assert!(result.is_clean);
        assert_eq!(result.matches, 2);
        assert!(result.mismatches.is_empty());
    }

    #[test]
    fn test_quantity_mismatch() {
        let mut portfolio = PortfolioState::new(10_000.0);
        portfolio.add_position(make_position(1, 100, 10.0));

        let broker = vec![BrokerPosition {
            ticker_id: TickerId(1),
            qty: 75,
            avg_cost: 10.0,
        }];

        let result = reconcile_positions(&portfolio, &broker);
        assert!(!result.is_clean);
        assert_eq!(result.mismatches.len(), 1);
        assert!(matches!(
            &result.mismatches[0],
            PositionMismatch::QuantityDiff {
                local_qty: 100,
                broker_qty: 75,
                ..
            }
        ));
    }

    #[test]
    fn test_broker_only_position() {
        let portfolio = PortfolioState::new(10_000.0);
        let broker = vec![BrokerPosition {
            ticker_id: TickerId(99),
            qty: 50,
            avg_cost: 15.0,
        }];

        let result = reconcile_positions(&portfolio, &broker);
        assert!(!result.is_clean);
        assert!(matches!(
            &result.mismatches[0],
            PositionMismatch::BrokerOnly { broker_qty: 50, .. }
        ));
    }

    #[test]
    fn test_local_only_position() {
        let mut portfolio = PortfolioState::new(10_000.0);
        portfolio.add_position(make_position(1, 100, 10.0));

        let result = reconcile_positions(&portfolio, &[]);
        assert!(!result.is_clean);
        assert!(matches!(
            &result.mismatches[0],
            PositionMismatch::LocalOnly { local_qty: 100, .. }
        ));
    }

    #[test]
    fn test_orphan_detection() {
        let known = vec!["order-1".to_string(), "order-2".to_string()];
        let broker_orders = vec![
            BrokerOpenOrder {
                order_id: "order-1".to_string(),
                ibkr_order_id: 1001,
                ticker_id: TickerId(1),
                qty: 100,
                status: "Submitted".to_string(),
            },
            BrokerOpenOrder {
                order_id: "order-999".to_string(),
                ibkr_order_id: 9999,
                ticker_id: TickerId(99),
                qty: 50,
                status: "Submitted".to_string(),
            },
        ];

        let orphans = detect_orphaned_orders(&known, &broker_orders);
        assert_eq!(orphans.len(), 1);
        assert_eq!(orphans[0], "order-999");
    }

    #[test]
    fn test_empty_both_sides() {
        let portfolio = PortfolioState::new(10_000.0);
        let result = reconcile_positions(&portfolio, &[]);
        assert!(result.is_clean);
        assert_eq!(result.matches, 0);
    }

    #[test]
    fn test_audit_log_new_is_empty() {
        let log = ReconcileAuditLog::new();
        assert!(log.is_empty());
        assert_eq!(log.len(), 0);
        assert!(!log.is_locked(0));
    }

    #[test]
    fn test_audit_log_record_triggers_lock() {
        let mut log = ReconcileAuditLog::new();
        let now = 1_000_000_000_000u64; // 1000s in ns
        log.record(
            PositionMismatch::QuantityDiff {
                ticker_id: TickerId(1),
                local_qty: 100,
                broker_qty: 50,
            },
            now,
        );
        assert_eq!(log.len(), 1);
        assert!(log.is_locked(now));
        // Still locked 1 hour later
        assert!(log.is_locked(now + 3_600_000_000_000));
        // Still locked 23h later
        assert!(log.is_locked(now + 23 * 3_600_000_000_000));
        // Unlocked after 24h
        assert!(!log.is_locked(now + 24 * 3_600_000_000_000));
    }

    #[test]
    fn test_audit_log_manual_clear() {
        let mut log = ReconcileAuditLog::new();
        let now = 1_000_000_000_000u64;
        log.record(
            PositionMismatch::BrokerOnly {
                ticker_id: TickerId(5),
                broker_qty: 200,
            },
            now,
        );
        assert!(log.is_locked(now));
        log.manual_clear_halt();
        // Immediately unlocked after manual clear
        assert!(!log.is_locked(now));
    }

    #[test]
    fn test_audit_log_default() {
        let log = ReconcileAuditLog::default();
        assert!(log.is_empty());
        assert!(!log.is_locked(0));
    }
}
