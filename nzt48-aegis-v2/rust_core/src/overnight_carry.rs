//! Phase 20: Overnight carry state machine.
//! Manages positions carried between trading sessions (Mode B → Dark → Mode A).
//! Chandelier stops are FROZEN during carry to prevent cross-timezone noise.

use std::collections::HashMap;

use crate::types::TickerId;

/// State of a position in the overnight carry lifecycle.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CarryState {
    /// Position is actively trading in the current session.
    Live,
    /// Position has been carried across a session boundary; stops frozen.
    Carried,
    /// Position is being monitored in pre-market / early session.
    Monitored,
    /// Position has been reactivated with updated price data.
    Reactivated,
}

/// A position carried across trading sessions.
#[derive(Clone, Debug)]
pub struct CarryPosition {
    /// Ticker identifier for the carried position.
    pub ticker_id: TickerId,
    /// Current carry lifecycle state.
    pub state: CarryState,
    /// Original entry price of the position.
    pub entry_price: f64,
    /// Chandelier stop frozen at session close — not updated until reactivation.
    pub frozen_stop: f64,
    /// Nanosecond timestamp when carry began.
    pub carry_start_ns: u64,
    /// Current unrealized P&L (GBP).
    pub unrealized_pnl: f64,
}

impl CarryPosition {
    /// Create a new carry position from a live position.
    pub fn new(ticker_id: TickerId, entry_price: f64, current_stop: f64, now_ns: u64) -> Self {
        Self {
            ticker_id,
            state: CarryState::Live,
            entry_price,
            frozen_stop: current_stop,
            carry_start_ns: now_ns,
            unrealized_pnl: 0.0,
        }
    }

    /// Transition from Live → Carried. Freezes the chandelier stop.
    pub fn transition_to_carry(&mut self, now_ns: u64) {
        if self.state == CarryState::Live {
            self.state = CarryState::Carried;
            self.carry_start_ns = now_ns;
        }
    }

    /// Transition from Carried → Monitored (pre-market observation phase).
    pub fn monitor(&mut self) {
        if self.state == CarryState::Carried {
            self.state = CarryState::Monitored;
        }
    }

    /// Reactivate a monitored position with fresh price data.
    /// Unfreezes the stop by allowing downstream recalculation from `new_price`.
    pub fn reactivate(&mut self, new_price: f64) {
        if self.state == CarryState::Monitored || self.state == CarryState::Carried {
            self.state = CarryState::Reactivated;
            // Update frozen_stop relative to new price — downstream exit engine
            // will recalculate the full chandelier, but we set a floor here.
            self.frozen_stop = self.frozen_stop.min(new_price * 0.97);
        }
    }

    /// Returns true if the chandelier stop is currently frozen (Carried or Monitored).
    pub fn is_stop_frozen(&self) -> bool {
        matches!(self.state, CarryState::Carried | CarryState::Monitored)
    }

    /// Update unrealized P&L.
    pub fn on_pnl_update(&mut self, pnl: f64) {
        self.unrealized_pnl = pnl;
    }
}

/// Manages all overnight carry positions.
#[derive(Clone, Debug, Default)]
pub struct CarryManager {
    positions: HashMap<TickerId, CarryPosition>,
}

impl CarryManager {
    pub fn new() -> Self {
        Self {
            positions: HashMap::new(),
        }
    }

    /// Transition all open positions to Carried state.
    pub fn carry_all_open(&mut self, now_ns: u64) {
        for pos in self.positions.values_mut() {
            pos.transition_to_carry(now_ns);
        }
    }

    /// Reactivate all carried/monitored positions with fresh prices.
    pub fn reactivate_all(&mut self, prices: &HashMap<TickerId, f64>) {
        for (tid, pos) in self.positions.iter_mut() {
            if let Some(&price) = prices.get(tid) {
                pos.reactivate(price);
            }
        }
    }

    /// Number of positions with frozen stops.
    pub fn frozen_count(&self) -> usize {
        self.positions.values().filter(|p| p.is_stop_frozen()).count()
    }

    /// Check if a position is currently in Carried state (stops frozen).
    pub fn is_carried(&self, ticker_id: &TickerId) -> bool {
        self.positions
            .get(ticker_id)
            .map(|p| p.is_stop_frozen())
            .unwrap_or(false)
    }

    /// Total unrealized P&L across all carry positions.
    pub fn total_carry_pnl(&self) -> f64 {
        self.positions.values().map(|p| p.unrealized_pnl).sum()
    }

    /// Insert or replace a carry position.
    pub fn insert(&mut self, pos: CarryPosition) {
        self.positions.insert(pos.ticker_id, pos);
    }

    /// Remove a position (e.g. after exit).
    pub fn remove(&mut self, ticker_id: &TickerId) -> Option<CarryPosition> {
        self.positions.remove(ticker_id)
    }

    /// Get a reference to a carry position.
    pub fn get(&self, ticker_id: &TickerId) -> Option<&CarryPosition> {
        self.positions.get(ticker_id)
    }

    /// Number of positions being managed.
    pub fn len(&self) -> usize {
        self.positions.len()
    }

    /// Whether the manager has no positions.
    pub fn is_empty(&self) -> bool {
        self.positions.is_empty()
    }

    /// P21: Freeze all stops for overnight carry (Mode B → Dark transition).
    /// Transitions all Live positions to Carried state.
    pub fn freeze_all_stops(&mut self, now_ns: u64) -> usize {
        let mut frozen_count = 0;
        for (ticker_id, pos) in self.positions.iter_mut() {
            if pos.state == CarryState::Live {
                pos.transition_to_carry(now_ns);
                frozen_count += 1;
                eprintln!(
                    "CARRY: position {} entered Carry state at Asian close (stop frozen at {:.4})",
                    ticker_id.0, pos.frozen_stop
                );
            }
        }
        if frozen_count > 0 {
            eprintln!("CARRY: froze {} positions at session boundary", frozen_count);
        }
        frozen_count
    }

    /// P21: Unfreeze all stops when returning to active session (Dark/Carry → Mode A/B).
    /// Transitions all Carried/Monitored positions to Reactivated.
    /// Note: Caller should provide fresh prices for proper reactivation.
    pub fn unfreeze_all_stops(&mut self) -> usize {
        let mut unfrozen_count = 0;
        for (ticker_id, pos) in self.positions.iter_mut() {
            if pos.state == CarryState::Carried || pos.state == CarryState::Monitored {
                let carry_duration_secs = pos.carry_start_ns as f64 / 1_000_000_000.0;
                pos.state = CarryState::Reactivated;
                unfrozen_count += 1;
                eprintln!(
                    "CARRY: position {} reactivated at European open (held {:.1}h, unrealized_pnl={:.2})",
                    ticker_id.0, carry_duration_secs / 3600.0, pos.unrealized_pnl
                );
            }
        }
        if unfrozen_count > 0 {
            eprintln!(
                "CARRY: unfroze {} positions at session boundary (total carry PnL={:.2})",
                unfrozen_count,
                self.total_carry_pnl()
            );
        }
        unfrozen_count
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_carry_lifecycle() {
        let mut pos = CarryPosition::new(TickerId(1), 100.0, 95.0, 1000);
        assert_eq!(pos.state, CarryState::Live);
        assert!(!pos.is_stop_frozen());

        pos.transition_to_carry(2000);
        assert_eq!(pos.state, CarryState::Carried);
        assert!(pos.is_stop_frozen());
        assert_eq!(pos.carry_start_ns, 2000);

        pos.monitor();
        assert_eq!(pos.state, CarryState::Monitored);
        assert!(pos.is_stop_frozen());

        pos.reactivate(105.0);
        assert_eq!(pos.state, CarryState::Reactivated);
        assert!(!pos.is_stop_frozen());
    }

    #[test]
    fn test_frozen_stop_not_updated_during_carry() {
        let mut pos = CarryPosition::new(TickerId(2), 50.0, 47.5, 1000);
        pos.transition_to_carry(2000);
        // Stop stays frozen at 47.5 during carry
        assert!((pos.frozen_stop - 47.5).abs() < f64::EPSILON);
        assert!(pos.is_stop_frozen());
    }

    #[test]
    fn test_pnl_update() {
        let mut pos = CarryPosition::new(TickerId(3), 200.0, 190.0, 1000);
        assert!((pos.unrealized_pnl - 0.0).abs() < f64::EPSILON);
        pos.on_pnl_update(15.50);
        assert!((pos.unrealized_pnl - 15.50).abs() < f64::EPSILON);
        pos.on_pnl_update(-3.25);
        assert!((pos.unrealized_pnl - -3.25).abs() < f64::EPSILON);
    }

    #[test]
    fn test_carry_manager_carry_and_reactivate() {
        let mut mgr = CarryManager::new();
        mgr.insert(CarryPosition::new(TickerId(1), 100.0, 95.0, 1000));
        mgr.insert(CarryPosition::new(TickerId(2), 200.0, 190.0, 1000));
        assert_eq!(mgr.len(), 2);
        assert_eq!(mgr.frozen_count(), 0);

        mgr.carry_all_open(2000);
        assert_eq!(mgr.frozen_count(), 2);

        let mut prices = HashMap::new();
        prices.insert(TickerId(1), 105.0);
        prices.insert(TickerId(2), 210.0);
        mgr.reactivate_all(&prices);
        assert_eq!(mgr.frozen_count(), 0);
    }

    #[test]
    fn test_carry_manager_total_pnl() {
        let mut mgr = CarryManager::new();
        let mut p1 = CarryPosition::new(TickerId(1), 100.0, 95.0, 1000);
        p1.on_pnl_update(10.0);
        let mut p2 = CarryPosition::new(TickerId(2), 200.0, 190.0, 1000);
        p2.on_pnl_update(-5.0);
        mgr.insert(p1);
        mgr.insert(p2);
        assert!((mgr.total_carry_pnl() - 5.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_carry_manager_remove() {
        let mut mgr = CarryManager::new();
        mgr.insert(CarryPosition::new(TickerId(7), 50.0, 48.0, 500));
        assert_eq!(mgr.len(), 1);
        let removed = mgr.remove(&TickerId(7));
        assert!(removed.is_some());
        assert!(mgr.is_empty());
    }

    #[test]
    fn test_transition_only_from_valid_state() {
        let mut pos = CarryPosition::new(TickerId(4), 100.0, 95.0, 1000);
        // monitor() should not work from Live state
        pos.monitor();
        assert_eq!(pos.state, CarryState::Live);

        // reactivate should not work from Live state
        pos.reactivate(110.0);
        assert_eq!(pos.state, CarryState::Live);

        // transition to carry, then reactivate — monitor should not work from Reactivated
        pos.transition_to_carry(2000);
        pos.reactivate(110.0);
        assert_eq!(pos.state, CarryState::Reactivated);
        pos.monitor();
        assert_eq!(pos.state, CarryState::Reactivated); // unchanged
    }

    // ── P21: Freeze/Unfreeze tests ──

    #[test]
    fn test_freeze_all_stops_transitions_live_to_carried() {
        let mut mgr = CarryManager::new();
        mgr.insert(CarryPosition::new(TickerId(1), 100.0, 95.0, 1000));
        mgr.insert(CarryPosition::new(TickerId(2), 200.0, 190.0, 1000));

        // Initially all Live (not frozen)
        assert_eq!(mgr.frozen_count(), 0);

        // Freeze all stops
        let frozen = mgr.freeze_all_stops(2000);

        // All should be Carried (frozen)
        assert_eq!(frozen, 2);
        assert_eq!(mgr.frozen_count(), 2);
        assert!(mgr.get(&TickerId(1)).unwrap().is_stop_frozen());
        assert!(mgr.get(&TickerId(2)).unwrap().is_stop_frozen());
    }

    #[test]
    fn test_unfreeze_all_stops_transitions_carried_to_reactivated() {
        let mut mgr = CarryManager::new();
        let mut p1 = CarryPosition::new(TickerId(1), 100.0, 95.0, 1000);
        p1.transition_to_carry(2000);
        let mut p2 = CarryPosition::new(TickerId(2), 200.0, 190.0, 1000);
        p2.transition_to_carry(2000);
        mgr.insert(p1);
        mgr.insert(p2);

        // All frozen
        assert_eq!(mgr.frozen_count(), 2);

        // Unfreeze all stops
        let unfrozen = mgr.unfreeze_all_stops();

        // All should be Reactivated (not frozen)
        assert_eq!(unfrozen, 2);
        assert_eq!(mgr.frozen_count(), 0);
        assert!(!mgr.get(&TickerId(1)).unwrap().is_stop_frozen());
        assert!(!mgr.get(&TickerId(2)).unwrap().is_stop_frozen());
    }

    #[test]
    fn test_freeze_all_stops_preserves_monitored() {
        let mut mgr = CarryManager::new();
        let mut p1 = CarryPosition::new(TickerId(1), 100.0, 95.0, 1000);
        p1.transition_to_carry(1000);
        p1.monitor();
        mgr.insert(p1);

        // Already monitored (frozen)
        assert_eq!(mgr.frozen_count(), 1);

        // Freeze all (shouldn't change Monitored positions)
        let frozen = mgr.freeze_all_stops(2000);

        // Still frozen, no new frozen count since it was already frozen
        assert_eq!(frozen, 0);
        assert_eq!(mgr.frozen_count(), 1);
        assert!(mgr.get(&TickerId(1)).unwrap().is_stop_frozen());
    }
}
