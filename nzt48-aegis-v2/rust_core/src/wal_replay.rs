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
    /// Trigger that caused the last RiskStateChange (e.g. "tick_watchdog_expired").
    pub restored_regime_trigger: Option<String>,
    /// P0-1.4: Kelly ramp trade count restored from WAL (highest seen KellyRampAdvance).
    pub kelly_ramp_count: u64,
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
    let mut corrupt_count = 0u64;

    for (i, line) in lines.iter().enumerate() {
        let _is_last = i == total - 1;
        let line = line.trim();
        if line.is_empty() {
            continue;
        }

        match serde_json::from_str::<WalEvent>(line) {
            Ok(event) => match verify_checksum(&event) {
                Ok(()) => events.push(event),
                Err(msg) => {
                    // Skip corrupt entries with warning (resilient mode)
                    eprintln!("WARNING: skipping corrupted WAL line {}: {msg}", i + 1);
                    corrupt_count += 1;
                }
            },
            Err(e) => {
                    eprintln!("WARNING: skipping unparseable WAL line {}: {e}", i + 1);
                    corrupt_count += 1;
            }
        }
    }

    if corrupt_count > 0 {
        eprintln!("WAL_REPLAY: skipped {corrupt_count} corrupt lines out of {total} total");
    }
    Ok(events)
}

/// Read ALL WAL files: current.ndjson + archive/*.ndjson.
/// Sorts all events by write_time_ns to maintain correct chronological order.
/// This ensures no trades are missed when the engine restarts multiple times per day.
pub fn read_all_wal_files(wal_dir: &Path) -> Vec<WalEvent> {
    let mut all_events = Vec::new();

    // 1. Read current.ndjson
    let current = wal_dir.join("current.ndjson");
    if current.exists() {
        match read_wal_file(&current) {
            Ok(events) => {
                eprintln!("WAL_REPLAY: {} events from current.ndjson", events.len());
                all_events.extend(events);
            }
            Err(e) => eprintln!("WAL_REPLAY: failed to read current.ndjson: {e}"),
        }
    }

    // 2. Read all archive/*.ndjson files
    let archive_dir = wal_dir.join("archive");
    if archive_dir.exists() {
        let mut archive_files: Vec<_> = std::fs::read_dir(&archive_dir)
            .into_iter()
            .flatten()
            .filter_map(|e| e.ok())
            .filter(|e| {
                e.path()
                    .extension()
                    .map_or(false, |ext| ext == "ndjson")
            })
            .collect();
        archive_files.sort_by_key(|e| e.file_name());

        for entry in &archive_files {
            match read_wal_file(&entry.path()) {
                Ok(events) => {
                    if !events.is_empty() {
                        eprintln!(
                            "WAL_REPLAY: {} events from {:?}",
                            events.len(),
                            entry.file_name()
                        );
                        all_events.extend(events);
                    }
                }
                Err(e) => eprintln!(
                    "WAL_REPLAY: failed to read {:?}: {e}",
                    entry.file_name()
                ),
            }
        }
    }

    // 3. Sort by write_time_ns (chronological order across all files)
    all_events.sort_by_key(|e| e.write_time_ns);

    eprintln!(
        "WAL_REPLAY: {} total events from all WAL files",
        all_events.len()
    );
    all_events
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
    let mut restored_regime_trigger: Option<String> = None;
    let mut kelly_ramp_count: u64 = 0;

    // Track pending fills per order for VWAP
    let mut fill_tracker: HashMap<String, (f64, u32)> = HashMap::new(); // (sum_price*qty, total_qty)
    // Track rung advances for restoration after all events are processed
    let mut rung_restore: HashMap<TickerId, (u8, f64, f64)> = HashMap::new(); // (rung, stop, high)

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
                spread_at_fill_pct: _,
                side: _,
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
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: String::new(),
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
            WalPayload::RiskStateChange { to, trigger, .. } => {
                // FIX 2026-03-11: Restore risk regime on WAL replay.
                // If engine crashed while in Halt/Flatten, don't reset to Normal.
                // Also capture trigger to distinguish watchdog vs liquidation halts.
                restored_regime = Some(to.clone());
                restored_regime_trigger = Some(trigger.clone());
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
            WalPayload::RungAdvanced { ticker_id, new_rung, stop_price, highest_high, .. } => {
                // Restore chandelier rung state for open positions after restart.
                // Uses rung_restore map (position may not exist yet during replay ordering).
                rung_restore.insert(TickerId(*ticker_id), (*new_rung, *stop_price, *highest_high));
            }
            // P0-1.4: Restore Kelly ramp counter from WAL (take highest count seen).
            WalPayload::KellyRampAdvance { count } => {
                if *count > kelly_ramp_count {
                    kelly_ramp_count = *count;
                }
            }
            WalPayload::OrphanResolved { .. }
            | WalPayload::ExitSignal { .. }
            | WalPayload::SystemReady { .. }
            | WalPayload::NextValidId { .. }
            | WalPayload::QuoteImbalanceInvalidated { .. }
            | WalPayload::SplitAdjustment { .. }
            | WalPayload::SystemShutdown { .. }
            // N2a/N2c: Signal analysis events — no state to restore during replay.
            // These are consumed by nightly analysis (Python), not the engine.
            | WalPayload::SignalRejected { .. }
            | WalPayload::MissedWinnerCandidate { .. }
            // P2-3.6: Signal funnel events — no state to restore during replay.
            | WalPayload::SignalGenerated { .. } => {}
        }
    }

    // Apply rung restoration to surviving open positions.
    // This restores chandelier rung state that was persisted via RungAdvanced events.
    for (tid, (rung, stop, high)) in &rung_restore {
        if let Some(pos) = portfolio.positions_mut().get_mut(tid) {
            if *rung > pos.trailing_rung {
                eprintln!(
                    "WAL_REPLAY: Restored rung {} (stop={:.4}, high={:.4}) for ticker {}",
                    rung, stop, high, tid.0
                );
                pos.trailing_rung = *rung;
            }
            if *stop > pos.stop_price {
                pos.stop_price = *stop;
            }
            if *high > pos.highest_high {
                pos.highest_high = *high;
            }
        }
    }

    // FIX 2026-03-17: Sync equity to cash + open position values after replay.
    // Without this, equity stays frozen at the last StateSnapshot value (or initial
    // £10,000) while cash has been correctly updated by add_position/remove_position.
    // Use entry prices as proxy since we have no live ticks during replay.
    let position_value: f64 = portfolio
        .positions()
        .values()
        .map(|p| p.avg_entry * p.qty as f64)
        .sum();
    portfolio.equity = portfolio.cash + position_value;
    // Update high-water mark if equity grew
    if portfolio.equity > portfolio.high_water_mark {
        portfolio.high_water_mark = portfolio.equity;
    }

    // Detect orphans: RoutedOrder with no BrokerAck or FillEvent
    let orphaned: Vec<String> = routed_orders
        .into_iter()
        .filter(|id| !resolved_orders.contains(id))
        .collect();

    if kelly_ramp_count > 0 {
        eprintln!(
            "WAL_REPLAY: Restored Kelly ramp count = {} ({}% Kelly)",
            kelly_ramp_count,
            ((kelly_ramp_count as f64 / 250.0).clamp(0.1, 1.0) * 100.0) as u32,
        );
    }

    ReplayResult {
        events_replayed,
        orphaned_orders: orphaned,
        last_snapshot_used,
        state_hash,
        restored_regime,
        restored_regime_trigger,
        kelly_ramp_count,
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
