//! SC-02: SubscriptionManager — lock-free active line count + MPSC actor for writes.
//! SC-11: active_line_count via AtomicUsize (no reqOpenOrders calls).
//!
//! Architecture:
//!   - Read path: AtomicUsize for lock-free active_line_count (hot path, every tick)
//!   - Write path: MPSC actor for add/remove operations (cold path)

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use crate::types::TickerId;
use crate::universe::UniverseClass;

/// Phase 11: Deferral window for recently cancelled subscriptions.
/// After cancellation, wait this long before allowing re-subscribe.
const DEFERRAL_COOLDOWN_NS: u64 = 2_000_000_000; // 2 seconds
/// Phase 11: How long to defer after cooldown check fails.
const DEFERRAL_DELAY_NS: u64 = 3_000_000_000; // 3 seconds

/// Subscription state per ticker.
#[derive(Clone, Debug)]
pub struct SubscriptionEntry {
    pub ticker_id: TickerId,
    pub class: UniverseClass,
    pub active: bool,
    /// Timestamp when subscription was last activated.
    pub activated_ns: u64,
}

/// Command for the subscription actor (write path).
#[derive(Debug)]
pub enum SubCommand {
    /// Add a new subscription.
    Add {
        ticker_id: TickerId,
        class: UniverseClass,
    },
    /// Remove (deactivate) a subscription.
    Remove { ticker_id: TickerId },
    /// Promote/demote a ticker's universe class.
    Reclassify {
        ticker_id: TickerId,
        new_class: UniverseClass,
    },
}

/// SC-02: Lock-free subscription manager.
/// Read path is zero-cost (Atomic load). Write path goes through a HashMap.
pub struct SubscriptionManager {
    /// Lock-free active count for hot-path reads (SC-11).
    active_count: Arc<AtomicUsize>,
    /// Subscription state per ticker (write path only).
    entries: HashMap<TickerId, SubscriptionEntry>,
    /// Maximum concurrent subscriptions (IBKR Semaphore(100)).
    max_subscriptions: usize,
    /// Phase 11: Recently cancelled tickers (ticker_id → cancellation timestamp_ns).
    recently_cancelled: HashMap<TickerId, u64>,
}

impl SubscriptionManager {
    pub fn new(max_subscriptions: usize) -> Self {
        Self {
            active_count: Arc::new(AtomicUsize::new(0)),
            entries: HashMap::new(),
            max_subscriptions,
            recently_cancelled: HashMap::new(),
        }
    }

    /// Lock-free read of active subscription count (SC-11).
    /// Called on every tick — must be O(1) with no locks.
    pub fn active_line_count(&self) -> usize {
        self.active_count.load(Ordering::Relaxed)
    }

    /// Get a clone of the atomic counter (for sharing across threads).
    pub fn active_count_handle(&self) -> Arc<AtomicUsize> {
        Arc::clone(&self.active_count)
    }

    /// Process a subscription command (write path).
    pub fn process_command(&mut self, cmd: SubCommand) {
        match cmd {
            SubCommand::Add { ticker_id, class } => {
                if self.entries.len() >= self.max_subscriptions {
                    eprintln!(
                        "SubMgr: at capacity ({}/{}), rejecting {:?}",
                        self.entries.len(),
                        self.max_subscriptions,
                        ticker_id
                    );
                    return;
                }
                let entry = SubscriptionEntry {
                    ticker_id,
                    class,
                    active: true,
                    activated_ns: 0, // Set by caller
                };
                self.entries.insert(ticker_id, entry);
                self.sync_count();
            }
            SubCommand::Remove { ticker_id } => {
                if let Some(entry) = self.entries.get_mut(&ticker_id) {
                    entry.active = false;
                }
                self.sync_count();
            }
            SubCommand::Reclassify {
                ticker_id,
                new_class,
            } => {
                if let Some(entry) = self.entries.get_mut(&ticker_id) {
                    entry.class = new_class;
                }
            }
        }
    }

    /// Check if a ticker has an active subscription.
    pub fn is_active(&self, ticker_id: &TickerId) -> bool {
        self.entries
            .get(ticker_id)
            .is_some_and(|e| e.active)
    }

    /// Get subscription entry for a ticker.
    pub fn get(&self, ticker_id: &TickerId) -> Option<&SubscriptionEntry> {
        self.entries.get(ticker_id)
    }

    /// Number of registered entries (active + inactive).
    pub fn total_entries(&self) -> usize {
        self.entries.len()
    }

    /// P21: Get list of currently active ticker IDs (for mode rotation).
    pub fn active_ticker_ids(&self) -> Vec<TickerId> {
        self.entries
            .values()
            .filter(|e| e.active)
            .map(|e| e.ticker_id)
            .collect()
    }

    /// Phase 11: Check if a ticker can be subscribed (not in deferral window).
    /// Returns true if allowed, false if recently cancelled (must wait 2s + 3s defer).
    pub fn can_subscribe(&self, ticker_id: &TickerId, now_ns: u64) -> bool {
        if let Some(&cancelled_at) = self.recently_cancelled.get(ticker_id) {
            let elapsed = now_ns.saturating_sub(cancelled_at);
            elapsed >= DEFERRAL_COOLDOWN_NS + DEFERRAL_DELAY_NS
        } else {
            true
        }
    }

    /// Phase 11: Record a cancellation for deferral tracking.
    pub fn record_cancellation(&mut self, ticker_id: TickerId, now_ns: u64) {
        self.recently_cancelled.insert(ticker_id, now_ns);
    }

    /// Phase 11: Clean up old deferral entries (> 10s old).
    pub fn prune_deferrals(&mut self, now_ns: u64) {
        let cutoff = now_ns.saturating_sub(10_000_000_000); // 10s
        self.recently_cancelled.retain(|_, &mut ts| ts > cutoff);
    }

    /// P21: Rotate subscriptions between modes.
    /// Deactivates all tickers, then activates only those in the new set.
    /// Returns the TickerIds that are now active.
    pub fn rotate_tickers(&mut self, new_tickers: Vec<TickerId>, now_ns: u64) -> Vec<TickerId> {
        // Mark all current subscriptions as inactive and record cancellations
        let to_cancel: Vec<TickerId> = self.entries
            .values()
            .filter(|e| e.active)
            .map(|e| e.ticker_id)
            .collect();

        for ticker_id in to_cancel {
            if let Some(entry) = self.entries.get_mut(&ticker_id) {
                entry.active = false;
            }
            self.record_cancellation(ticker_id, now_ns);
        }

        // Activate the new set
        let mut activated = Vec::new();
        for ticker_id in new_tickers {
            if let Some(entry) = self.entries.get_mut(&ticker_id) {
                // Already registered — just reactivate
                entry.active = true;
                entry.activated_ns = now_ns;
                activated.push(ticker_id);
            } else if self.entries.len() < self.max_subscriptions {
                // New ticker — add and activate
                let entry = SubscriptionEntry {
                    ticker_id,
                    class: UniverseClass::Vanguard,
                    active: true,
                    activated_ns: now_ns,
                };
                self.entries.insert(ticker_id, entry);
                activated.push(ticker_id);
            } else {
                eprintln!(
                    "SubMgr: at capacity ({}/{}), cannot activate {:?}",
                    self.entries.len(),
                    self.max_subscriptions,
                    ticker_id
                );
            }
        }

        self.sync_count();
        activated
    }

    /// Sync the atomic counter with the actual active count.
    fn sync_count(&self) {
        let count = self.entries.values().filter(|e| e.active).count();
        self.active_count.store(count, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sub_manager_add_remove() {
        let mut mgr = SubscriptionManager::new(100);
        assert_eq!(mgr.active_line_count(), 0);

        mgr.process_command(SubCommand::Add {
            ticker_id: TickerId(1),
            class: UniverseClass::Vanguard,
        });
        assert_eq!(mgr.active_line_count(), 1);
        assert!(mgr.is_active(&TickerId(1)));

        mgr.process_command(SubCommand::Remove {
            ticker_id: TickerId(1),
        });
        assert_eq!(mgr.active_line_count(), 0);
        assert!(!mgr.is_active(&TickerId(1)));
    }

    #[test]
    fn test_sub_manager_capacity_limit() {
        let mut mgr = SubscriptionManager::new(2);
        mgr.process_command(SubCommand::Add {
            ticker_id: TickerId(1),
            class: UniverseClass::Vanguard,
        });
        mgr.process_command(SubCommand::Add {
            ticker_id: TickerId(2),
            class: UniverseClass::Apex,
        });
        // At capacity — third should be rejected
        mgr.process_command(SubCommand::Add {
            ticker_id: TickerId(3),
            class: UniverseClass::Vanguard,
        });
        assert_eq!(mgr.total_entries(), 2);
        assert_eq!(mgr.active_line_count(), 2);
    }

    #[test]
    fn test_sub_manager_reclassify() {
        let mut mgr = SubscriptionManager::new(100);
        mgr.process_command(SubCommand::Add {
            ticker_id: TickerId(1),
            class: UniverseClass::Vanguard,
        });
        assert_eq!(
            mgr.get(&TickerId(1)).map(|e| e.class),
            Some(UniverseClass::Vanguard)
        );

        mgr.process_command(SubCommand::Reclassify {
            ticker_id: TickerId(1),
            new_class: UniverseClass::Apex,
        });
        assert_eq!(
            mgr.get(&TickerId(1)).map(|e| e.class),
            Some(UniverseClass::Apex)
        );
    }

    #[test]
    fn test_sub_manager_atomic_handle_shared() {
        let mgr = SubscriptionManager::new(100);
        let handle = mgr.active_count_handle();
        // Both read the same counter
        assert_eq!(handle.load(Ordering::Relaxed), 0);
        assert_eq!(mgr.active_line_count(), 0);
    }

    // ── Phase 11: Deferral window tests ──

    #[test]
    fn test_deferral_blocks_resubscribe_within_window() {
        let mut mgr = SubscriptionManager::new(100);
        let tid = TickerId(42);
        let cancel_time = 10_000_000_000u64; // 10s

        mgr.record_cancellation(tid, cancel_time);

        // Immediately after cancel: blocked
        assert!(!mgr.can_subscribe(&tid, cancel_time));
        // 2s later (within cooldown): still blocked
        assert!(!mgr.can_subscribe(&tid, cancel_time + 2_000_000_000));
        // 4s later (past cooldown but within cooldown+delay): still blocked
        assert!(!mgr.can_subscribe(&tid, cancel_time + 4_000_000_000));
        // 5s later (exactly at cooldown+delay boundary): allowed
        assert!(mgr.can_subscribe(&tid, cancel_time + 5_000_000_000));
        // 6s later: allowed
        assert!(mgr.can_subscribe(&tid, cancel_time + 6_000_000_000));
    }

    #[test]
    fn test_deferral_unaffected_ticker_always_allowed() {
        let mgr = SubscriptionManager::new(100);
        // Ticker with no cancellation record is always allowed
        assert!(mgr.can_subscribe(&TickerId(99), 1_000_000_000));
    }

    #[test]
    fn test_deferral_double_cancellation_resets_window() {
        let mut mgr = SubscriptionManager::new(100);
        let tid = TickerId(7);

        mgr.record_cancellation(tid, 10_000_000_000);
        // Re-cancel at 12s — resets the deferral window
        mgr.record_cancellation(tid, 12_000_000_000);

        // 14s: only 2s after second cancel, should be blocked
        assert!(!mgr.can_subscribe(&tid, 14_000_000_000));
        // 17s: 5s after second cancel, should be allowed
        assert!(mgr.can_subscribe(&tid, 17_000_000_000));
    }

    #[test]
    fn test_prune_deferrals_removes_old_entries() {
        let mut mgr = SubscriptionManager::new(100);
        mgr.record_cancellation(TickerId(1), 1_000_000_000);  // 1s
        mgr.record_cancellation(TickerId(2), 5_000_000_000);  // 5s
        mgr.record_cancellation(TickerId(3), 15_000_000_000); // 15s

        // Prune at 20s: cutoff = 20s - 10s = 10s. Entries at 1s and 5s are pruned.
        mgr.prune_deferrals(20_000_000_000);

        // Ticker 1 and 2 pruned (old) → allowed
        assert!(mgr.can_subscribe(&TickerId(1), 20_000_000_000));
        assert!(mgr.can_subscribe(&TickerId(2), 20_000_000_000));
        // Ticker 3 still in deferral map (15s > 10s cutoff)
        // But 20s - 15s = 5s >= cooldown+delay, so it passes can_subscribe anyway
        assert!(mgr.can_subscribe(&TickerId(3), 20_000_000_000));
    }

    #[test]
    fn test_prune_deferrals_keeps_recent() {
        let mut mgr = SubscriptionManager::new(100);
        mgr.record_cancellation(TickerId(1), 18_000_000_000); // 18s

        // Prune at 20s: cutoff = 10s. 18s > 10s, so NOT pruned.
        mgr.prune_deferrals(20_000_000_000);

        // 20s - 18s = 2s < 5s required → still blocked
        assert!(!mgr.can_subscribe(&TickerId(1), 20_000_000_000));
    }

    // ── P21: Rotation tests ──

    #[test]
    fn test_rotate_tickers_deactivates_old_activates_new() {
        let mut mgr = SubscriptionManager::new(100);
        let t1 = TickerId(1);
        let t2 = TickerId(2);
        let t3 = TickerId(3);

        // Add some initial tickers
        mgr.process_command(SubCommand::Add {
            ticker_id: t1,
            class: UniverseClass::Vanguard,
        });
        mgr.process_command(SubCommand::Add {
            ticker_id: t2,
            class: UniverseClass::Vanguard,
        });
        assert_eq!(mgr.active_line_count(), 2);

        // Rotate to new set (t2, t3)
        let activated = mgr.rotate_tickers(vec![t2, t3], 10_000_000_000);

        // t1 should be inactive, t2 should stay active, t3 should be new+active
        assert!(!mgr.is_active(&t1));
        assert!(mgr.is_active(&t2));
        assert!(mgr.is_active(&t3));
        assert_eq!(mgr.active_line_count(), 2);
        assert_eq!(activated.len(), 2);
        assert!(activated.contains(&t2));
        assert!(activated.contains(&t3));
    }

    #[test]
    fn test_rotate_tickers_records_cancellations() {
        let mut mgr = SubscriptionManager::new(100);
        let t1 = TickerId(1);
        let t2 = TickerId(2);

        mgr.process_command(SubCommand::Add {
            ticker_id: t1,
            class: UniverseClass::Vanguard,
        });

        let now = 1_000_000_000u64;
        mgr.rotate_tickers(vec![t2], now);

        // t1 was deactivated and should be in deferral window
        assert!(!mgr.can_subscribe(&t1, now));
        // Must wait 5s to resubscribe
        assert!(mgr.can_subscribe(&t1, now + 5_000_000_000));
    }

    #[test]
    fn test_rotate_tickers_empty_new_set() {
        let mut mgr = SubscriptionManager::new(100);
        let t1 = TickerId(1);

        mgr.process_command(SubCommand::Add {
            ticker_id: t1,
            class: UniverseClass::Vanguard,
        });
        assert_eq!(mgr.active_line_count(), 1);

        // Rotate to empty set (dark hours)
        let activated = mgr.rotate_tickers(vec![], 10_000_000_000);

        // Everything deactivated
        assert!(!mgr.is_active(&t1));
        assert_eq!(mgr.active_line_count(), 0);
        assert_eq!(activated.len(), 0);
    }

    #[test]
    fn test_rotate_tickers_preserves_registrations() {
        let mut mgr = SubscriptionManager::new(100);
        let t1 = TickerId(1);
        let t2 = TickerId(2);

        // Register t1 and t2
        mgr.process_command(SubCommand::Add {
            ticker_id: t1,
            class: UniverseClass::Vanguard,
        });
        mgr.process_command(SubCommand::Add {
            ticker_id: t2,
            class: UniverseClass::Apex,
        });

        // Rotate to only t1
        mgr.rotate_tickers(vec![t1], 10_000_000_000);

        // Total entries should still be 2 (just t2 is inactive)
        assert_eq!(mgr.total_entries(), 2);
        assert!(mgr.is_active(&t1));
        assert!(!mgr.is_active(&t2));
        assert_eq!(mgr.active_line_count(), 1);
    }
}
