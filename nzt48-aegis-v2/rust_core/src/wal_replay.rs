//! WAL Replay — reads ndjson journal, verifies CRC32, reconstructs state.
//! Handles corruption, orphan detection, snapshot recovery, idempotent replay.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::io::{BufRead, BufReader};
use std::path::Path;

use crate::portfolio::PortfolioState;
use crate::types::{OrderState, PositionState, TickerId};
use crate::types::{WalEvent, WalPayload};
use crate::wal_writer::WalError;

/// Result of a WAL replay operation.
#[derive(Debug)]
pub struct ReplayResult {
    pub events_replayed: u64,
    pub orphaned_orders: Vec<String>,
    pub last_snapshot_used: bool,
    pub state_hash: Option<String>,
    /// Last RiskStateChange regime from WAL (for persistence across restarts).
    pub restored_regime: Option<String>,
}

/// Verify a single WAL line's CRC32 integrity.
fn verify_checksum(event: &WalEvent) -> Result<(), String> {
    let payload_json =
        serde_json::to_string(&event.payload).map_err(|e| format!("serialize: {e}"))?;
    let computed = crc32fast::hash(payload_json.as_bytes());
    if computed != event.checksum {
        return Err(format!(
            "CRC32 mismatch: stored={}, computed={} for event {}",
            event.checksum, computed, event.event_id
        ));
    }
    Ok(())
}

/// Parse and verify all WAL lines from a file.
/// Returns events or panics on non-last-line corruption (H27).
pub fn read_wal_file(path: &Path) -> Result<Vec<WalEvent>, WalError> {
    let file = fs::File::open(path)?;
    let reader = BufReader::new(file);
    let lines: Vec<String> = reader.lines().collect::<Result<Vec<_>, _>>()?;
    let total = lines.len();
    let mut events = Vec::with_capacity(total);

    for (i, line) in lines.iter().enumerate() {
        let is_last = i == total - 1;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        match serde_json::from_str::<WalEvent>(line) {
            Ok(event) => match verify_checksum(&event) {
                Ok(()) => events.push(event),
                Err(msg) => {
                    if is_last {
                        // Last line corruption → skip with WARNING (H27)
                        eprintln!("WARNING: skipping corrupted last WAL line: {msg}");
                    } else {
                        // Non-last line corruption → panic! (H27)
                        panic!("FATAL: WAL corruption on line {}: {msg}", i + 1);
                    }
                }
            },
            Err(e) => {
                if is_last {
                    eprintln!("WARNING: skipping unparseable last WAL line: {e}");
                } else {
                    panic!("FATAL: WAL corruption on line {}: {e}", i + 1);
                }
            }
        }
    }

    Ok(events)
}

/// Replay WAL events into a PortfolioState.
/// Handles: RoutedOrder, FillEvent, PositionClosed, StateSnapshot, RiskStateChange.
pub fn replay_events(events: &[WalEvent], portfolio: &mut PortfolioState) -> ReplayResult {
    let mut routed_orders: HashSet<String> = HashSet::new();
    let mut resolved_orders: HashSet<String> = HashSet::new();
    let mut fill_exec_ids: HashSet<String> = HashSet::new();
    let mut events_replayed = 0u64;
    let mut last_snapshot_used = false;
    let mut state_hash: Option<String> = None;
    let mut restored_regime: Option<String> = None;

    // Track pending fills per order for VWAP
    let mut fill_tracker: HashMap<String, (f64, u32)> = HashMap::new(); // (sum_price*qty, total_qty)

    for event in events {
        events_replayed += 1;
        match &event.payload {
            WalPayload::StateSnapshot {
                equity,
                high_water,
                hash,
                ..
            } => {
                portfolio.equity = *equity;
                portfolio.high_water_mark = *high_water;
                state_hash = Some(hash.clone());
                last_snapshot_used = true;
            }
            WalPayload::RoutedOrder {
                order_id,
                ticker_id,
                side,
                ..
            } => {
                if side != "Short" {
                    routed_orders.insert(order_id.clone());
                    portfolio.set_pending_count(
                        portfolio.total_position_count() + 1 - portfolio.filled_count(),
                    );
                    let _ = (ticker_id, side); // Used for tracking
                }
            }
            WalPayload::BrokerAck {
                order_id, status, ..
            } => {
                resolved_orders.insert(order_id.clone());
                if status == "Rejected" {
                    let pending = portfolio
                        .total_position_count()
                        .saturating_sub(portfolio.filled_count());
                    portfolio.set_pending_count(pending.saturating_sub(1));
                }
            }
            WalPayload::FillEvent {
                order_id,
                ticker_id,
                filled_qty,
                remaining_qty,
                price,
                exec_id,
                commission,
            } => {
                // Deduplication by exec_id
                if fill_exec_ids.contains(exec_id) {
                    continue;
                }
                fill_exec_ids.insert(exec_id.clone());
                resolved_orders.insert(order_id.clone());

                // Track VWAP
                let entry = fill_tracker.entry(order_id.clone()).or_insert((0.0, 0));
                entry.0 += price * (*filled_qty as f64);
                entry.1 += filled_qty;

                if *remaining_qty == 0 {
                    let (sum, total) = fill_tracker
                        .get(order_id)
                        .copied()
                        .unwrap_or((*price * *filled_qty as f64, *filled_qty));
                    let vwap = if total > 0 {
                        sum / total as f64
                    } else {
                        *price
                    };
                    let pos = PositionState {
                        entry_timestamp_ns: event.event_time_ns,
                        avg_entry: vwap,
                        unrealized_pnl: 0.0,
                        realized_pnl: 0.0,
                        highest_high: vwap,
                        stop_price: vwap * 0.95, // Default, real stop set by exit engine
                        total_commission: *commission,
                        qty: total,
                        ticker_id: TickerId(*ticker_id),
                        trailing_rung: 0,
                        state: OrderState::Filled,
                        origin_order_id: order_id.clone(),
                        is_carried: false,
                    };
                    portfolio.add_position(pos);
                    let pending = portfolio
                        .total_position_count()
                        .saturating_sub(portfolio.filled_count());
                    portfolio.set_pending_count(pending.saturating_sub(1));
                }
            }
            WalPayload::PositionClosed {
                ticker_id,
                final_pnl,
                ..
            } => {
                portfolio.remove_position(TickerId(*ticker_id));
                portfolio.daily_pnl += final_pnl;
            }
            WalPayload::RiskStateChange { to, .. } => {
                // FIX 2026-03-11: Restore risk regime on WAL replay.
                // If engine crashed while in Halt/Flatten, don't reset to Normal.
                restored_regime = Some(to.clone());
            }
            WalPayload::ReconciliationDivergence { .. } => {
                // P2-A: Record that a divergence was detected.
                // If no subsequent ReconciliationCleared, replay will set regime to Halt.
                restored_regime = Some("Halt".to_string());
            }
            WalPayload::ReconciliationCleared { .. } => {
                // P2-A: Divergence cleared — restore regime to Normal.
                if restored_regime.as_deref() == Some("Halt") {
                    restored_regime = Some("Normal".to_string());
                }
            }
            WalPayload::DailyReset { .. } => {
                // P2-C: Daily reset replayed — reset daily counters.
                portfolio.daily_pnl = 0.0;
                portfolio.consecutive_stop_losses = 0;
            }
            WalPayload::OrphanResolved { .. }
            | WalPayload::ExitSignal { .. }
            | WalPayload::SystemReady { .. }
            | WalPayload::NextValidId { .. }
            | WalPayload::QuoteImbalanceInvalidated { .. }
            | WalPayload::SplitAdjustment { .. }
            | WalPayload::SystemShutdown { .. } => {}
        }
    }

    // Detect orphans: RoutedOrder with no BrokerAck or FillEvent
    let orphaned: Vec<String> = routed_orders
        .into_iter()
        .filter(|id| !resolved_orders.contains(id))
        .collect();

    ReplayResult {
        events_replayed,
        orphaned_orders: orphaned,
        last_snapshot_used,
        state_hash,
        restored_regime,
    }
}

/// Replay from snapshot: find last StateSnapshot, replay only events after it.
pub fn replay_from_snapshot(events: &[WalEvent], portfolio: &mut PortfolioState) -> ReplayResult {
    // Find the last StateSnapshot index
    let snapshot_idx = events
        .iter()
        .rposition(|e| matches!(e.payload, WalPayload::StateSnapshot { .. }));

    match snapshot_idx {
        Some(idx) => replay_events(&events[idx..], portfolio),
        None => replay_events(events, portfolio),
    }
}

/// Compute a simple deterministic hash of portfolio state (H85).
pub fn compute_state_hash(portfolio: &PortfolioState) -> String {
    let mut hasher = crc32fast::Hasher::new();
    // Hash equity + cash + position count
    hasher.update(&portfolio.equity.to_le_bytes());
    hasher.update(&portfolio.cash.to_le_bytes());
    hasher.update(&portfolio.filled_count().to_le_bytes());
    hasher.update(&portfolio.high_water_mark.to_le_bytes());
    hasher.update(&portfolio.daily_pnl.to_le_bytes());
    // Hash each position's key fields (sorted for determinism)
    let mut tickers: Vec<_> = portfolio.positions().keys().collect();
    tickers.sort_by_key(|t| t.0);
    for tid in tickers {
        if let Some(pos) = portfolio.get_position(tid) {
            hasher.update(&tid.0.to_le_bytes());
            hasher.update(&pos.qty.to_le_bytes());
            hasher.update(&pos.avg_entry.to_le_bytes());
            hasher.update(&pos.stop_price.to_le_bytes());
        }
    }
    format!("{:08x}", hasher.finalize())
}
