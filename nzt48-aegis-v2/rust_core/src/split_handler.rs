//! Split Adjustment Handler — adjusts positions and prices for corporate stock splits.
//!
//! Handles forward splits (e.g. 2:1), reverse splits (e.g. 1:10),
//! and prevents double-processing of the same split event.

use crate::types::TickerId;

/// A corporate split event for a given ticker.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SplitEvent {
    pub ticker_id: TickerId,
    /// Number of old shares (e.g. 1 in a 2:1 split).
    pub ratio_from: u32,
    /// Number of new shares (e.g. 2 in a 2:1 split).
    pub ratio_to: u32,
    /// Effective timestamp in nanoseconds since epoch.
    pub effective_ns: u64,
}

/// The result of applying a split to a position.
#[derive(Clone, Debug, PartialEq)]
pub struct SplitAdjustment {
    pub new_qty: i64,
    pub new_entry_price: f64,
    pub new_stop_price: f64,
}

/// Tracks and applies split events, preventing double-processing.
#[derive(Clone, Debug, Default)]
pub struct SplitHandler {
    processed: Vec<SplitEvent>,
}

impl SplitHandler {
    /// Create a new empty handler.
    pub fn new() -> Self {
        Self {
            processed: Vec::new(),
        }
    }

    /// Return the split ratio as f64 (ratio_to / ratio_from).
    /// For a 2:1 split this returns 2.0; for a 1:10 reverse split this returns 0.1.
    pub fn split_ratio(event: &SplitEvent) -> f64 {
        event.ratio_to as f64 / event.ratio_from as f64
    }

    /// Apply a split to the given position, returning adjusted qty and prices.
    /// Quantity is multiplied by the ratio; prices are divided by the ratio.
    pub fn apply_split(
        &self,
        event: &SplitEvent,
        current_qty: i64,
        entry_price: f64,
        stop_price: f64,
    ) -> SplitAdjustment {
        let ratio = Self::split_ratio(event);
        // For qty: multiply by ratio_to, divide by ratio_from (integer arithmetic
        // to preserve exactness for clean splits).
        let new_qty = current_qty * event.ratio_to as i64 / event.ratio_from as i64;
        let new_entry_price = entry_price / ratio;
        let new_stop_price = stop_price / ratio;
        SplitAdjustment {
            new_qty,
            new_entry_price,
            new_stop_price,
        }
    }

    /// Check whether a split for the given ticker at the given effective time
    /// has already been processed.
    pub fn was_processed(&self, ticker_id: TickerId, effective_ns: u64) -> bool {
        self.processed
            .iter()
            .any(|e| e.ticker_id == ticker_id && e.effective_ns == effective_ns)
    }

    /// Record a split event as processed to prevent double-application.
    pub fn record_processed(&mut self, event: SplitEvent) {
        self.processed.push(event);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_event(ticker: u32, from: u32, to: u32, ns: u64) -> SplitEvent {
        SplitEvent {
            ticker_id: TickerId(ticker),
            ratio_from: from,
            ratio_to: to,
            effective_ns: ns,
        }
    }

    #[test]
    fn test_standard_2_for_1_split() {
        let handler = SplitHandler::new();
        let event = make_event(1, 1, 2, 1_000_000);
        let adj = handler.apply_split(&event, 100, 200.0, 180.0);
        assert_eq!(adj.new_qty, 200);
        assert!((adj.new_entry_price - 100.0).abs() < 1e-10);
        assert!((adj.new_stop_price - 90.0).abs() < 1e-10);
    }

    #[test]
    fn test_reverse_split_1_for_10() {
        let handler = SplitHandler::new();
        let event = make_event(2, 10, 1, 2_000_000);
        let adj = handler.apply_split(&event, 1000, 5.0, 4.0);
        assert_eq!(adj.new_qty, 100);
        assert!((adj.new_entry_price - 50.0).abs() < 1e-10);
        assert!((adj.new_stop_price - 40.0).abs() < 1e-10);
    }

    #[test]
    fn test_3_for_1_split() {
        let handler = SplitHandler::new();
        let event = make_event(3, 1, 3, 3_000_000);
        let adj = handler.apply_split(&event, 50, 300.0, 270.0);
        assert_eq!(adj.new_qty, 150);
        assert!((adj.new_entry_price - 100.0).abs() < 1e-10);
        assert!((adj.new_stop_price - 90.0).abs() < 1e-10);
    }

    #[test]
    fn test_no_double_processing() {
        let mut handler = SplitHandler::new();
        let event = make_event(1, 1, 2, 1_000_000);

        assert!(!handler.was_processed(TickerId(1), 1_000_000));
        handler.record_processed(event);
        assert!(handler.was_processed(TickerId(1), 1_000_000));

        // Different ticker or timestamp should not match.
        assert!(!handler.was_processed(TickerId(2), 1_000_000));
        assert!(!handler.was_processed(TickerId(1), 9_999_999));
    }

    #[test]
    fn test_price_adjustment_precision() {
        let handler = SplitHandler::new();
        // 4:1 split
        let event = make_event(5, 1, 4, 5_000_000);
        let adj = handler.apply_split(&event, 10, 1000.0, 950.0);
        assert_eq!(adj.new_qty, 40);
        assert!((adj.new_entry_price - 250.0).abs() < 1e-10);
        assert!((adj.new_stop_price - 237.5).abs() < 1e-10);
    }

    #[test]
    fn test_split_ratio_values() {
        let fwd = make_event(1, 1, 2, 0);
        assert!((SplitHandler::split_ratio(&fwd) - 2.0).abs() < 1e-10);

        let rev = make_event(1, 10, 1, 0);
        assert!((SplitHandler::split_ratio(&rev) - 0.1).abs() < 1e-10);

        let three_for_two = make_event(1, 2, 3, 0);
        assert!((SplitHandler::split_ratio(&three_for_two) - 1.5).abs() < 1e-10);
    }

    #[test]
    fn test_short_position_split() {
        let handler = SplitHandler::new();
        let event = make_event(7, 1, 2, 7_000_000);
        // Negative qty represents a short position.
        let adj = handler.apply_split(&event, -100, 200.0, 220.0);
        assert_eq!(adj.new_qty, -200);
        assert!((adj.new_entry_price - 100.0).abs() < 1e-10);
        assert!((adj.new_stop_price - 110.0).abs() < 1e-10);
    }
}
