//! L2 Order Book — maintains multi-level depth per ticker from reqMktDepth.
//!
//! Stores 5 levels of bid/ask (price, size) and computes aggregate metrics
//! for the Python bridge: depth imbalance, book pressure, wall prices, spreads.
//!
//! IBKR sends updateMktDepth callbacks with:
//!   position (0-4), operation (0=insert, 1=update, 2=delete), side (0=ask, 1=bid), price, size.

use std::collections::HashMap;
use crate::types::TickerId;

/// Number of price levels tracked per side.
pub const DEPTH_LEVELS: usize = 5;

/// A single price level in the order book.
#[derive(Clone, Copy, Debug, Default)]
pub struct PriceLevel {
    pub price: f64,
    pub size: f64,
}

/// Multi-level order book for a single ticker.
/// Maintains up to DEPTH_LEVELS on each side (bid/ask).
#[derive(Clone, Debug)]
pub struct OrderBook {
    pub bids: [PriceLevel; DEPTH_LEVELS],
    pub asks: [PriceLevel; DEPTH_LEVELS],
    pub last_update_ns: u64,
    /// Number of updates received (for staleness detection).
    pub update_count: u64,
}

impl Default for OrderBook {
    fn default() -> Self {
        Self {
            bids: [PriceLevel::default(); DEPTH_LEVELS],
            asks: [PriceLevel::default(); DEPTH_LEVELS],
            last_update_ns: 0,
            update_count: 0,
        }
    }
}

impl OrderBook {
    /// Apply a depth update from IBKR's updateMktDepth callback.
    ///
    /// - `position`: row index 0..4
    /// - `operation`: 0=insert, 1=update, 2=delete
    /// - `side`: 0=ask, 1=bid (IBKR convention)
    /// - `price`: order price
    /// - `size`: order size
    pub fn apply_update(
        &mut self,
        position: i32,
        operation: i32,
        side: i32,
        price: f64,
        size: f64,
        now_ns: u64,
    ) {
        let pos = position as usize;
        if pos >= DEPTH_LEVELS {
            return; // Out of range — ignore
        }

        let levels = if side == 1 { &mut self.bids } else { &mut self.asks };

        match operation {
            0 | 1 => {
                // Insert or Update
                levels[pos] = PriceLevel { price, size };
            }
            2 => {
                // Delete
                levels[pos] = PriceLevel::default();
            }
            _ => {} // Unknown operation
        }

        self.last_update_ns = now_ns;
        self.update_count += 1;
    }

    /// Check if the book has any data (at least one non-zero bid and ask).
    pub fn has_data(&self) -> bool {
        let has_bid = self.bids.iter().any(|l| l.price > 0.0 && l.size > 0.0);
        let has_ask = self.asks.iter().any(|l| l.price > 0.0 && l.size > 0.0);
        has_bid && has_ask
    }
}

/// Aggregate depth metrics computed from an OrderBook.
/// These fields are sent to Python via the bridge.
#[derive(Clone, Debug, Default)]
pub struct DepthMetrics {
    /// Sum of all 5 bid sizes.
    pub total_bid_depth: f64,
    /// Sum of all 5 ask sizes.
    pub total_ask_depth: f64,
    /// (total_bid - total_ask) / (total_bid + total_ask), range [-1, 1].
    pub depth_imbalance: f64,
    /// Price level with the largest bid size.
    pub bid_wall_price: f64,
    /// Price level with the largest ask size.
    pub ask_wall_price: f64,
    /// Top-of-book spread: asks[0].price - bids[0].price.
    pub spread_depth_1: f64,
    /// Full book spread: asks[4].price - bids[4].price (outermost levels).
    pub spread_depth_5: f64,
    /// Weighted book pressure: sum(size * inverse_distance_from_mid) for each level.
    /// Positive = bid-heavy (bullish), negative = ask-heavy (bearish).
    pub book_pressure: f64,
}

impl DepthMetrics {
    /// Compute aggregate depth metrics from an OrderBook.
    pub fn from_book(book: &OrderBook) -> Self {
        let mut total_bid: f64 = 0.0;
        let mut total_ask: f64 = 0.0;
        let mut bid_wall = PriceLevel::default();
        let mut ask_wall = PriceLevel::default();

        for level in &book.bids {
            if level.size > 0.0 {
                total_bid += level.size;
                if level.size > bid_wall.size {
                    bid_wall = *level;
                }
            }
        }

        for level in &book.asks {
            if level.size > 0.0 {
                total_ask += level.size;
                if level.size > ask_wall.size {
                    ask_wall = *level;
                }
            }
        }

        let total = total_bid + total_ask;
        let depth_imbalance = if total > 0.0 {
            (total_bid - total_ask) / total
        } else {
            0.0
        };

        // Top-of-book spread (level 0)
        let spread_depth_1 = if book.asks[0].price > 0.0 && book.bids[0].price > 0.0 {
            book.asks[0].price - book.bids[0].price
        } else {
            0.0
        };

        // Full book spread (outermost populated levels)
        let outermost_ask = book.asks.iter().rev()
            .find(|l| l.price > 0.0)
            .map(|l| l.price)
            .unwrap_or(0.0);
        let outermost_bid = book.bids.iter().rev()
            .find(|l| l.price > 0.0)
            .map(|l| l.price)
            .unwrap_or(0.0);
        let spread_depth_5 = if outermost_ask > 0.0 && outermost_bid > 0.0 {
            outermost_ask - outermost_bid
        } else {
            0.0
        };

        // Book pressure: weighted sum where levels closer to mid have more weight.
        // mid = (best_bid + best_ask) / 2
        // For each bid level: +size / distance_from_mid
        // For each ask level: -size / distance_from_mid
        let mid = if book.bids[0].price > 0.0 && book.asks[0].price > 0.0 {
            (book.bids[0].price + book.asks[0].price) / 2.0
        } else {
            0.0
        };

        let mut book_pressure = 0.0;
        if mid > 0.0 {
            for level in &book.bids {
                if level.price > 0.0 && level.size > 0.0 {
                    let dist = (mid - level.price).abs().max(0.0001); // Avoid div-by-zero
                    book_pressure += level.size / dist;
                }
            }
            for level in &book.asks {
                if level.price > 0.0 && level.size > 0.0 {
                    let dist = (level.price - mid).abs().max(0.0001);
                    book_pressure -= level.size / dist;
                }
            }
        }

        Self {
            total_bid_depth: total_bid,
            total_ask_depth: total_ask,
            depth_imbalance,
            bid_wall_price: bid_wall.price,
            ask_wall_price: ask_wall.price,
            spread_depth_1,
            spread_depth_5,
            book_pressure,
        }
    }
}

/// Per-ticker order book store.
/// Used by IbkrBroker to maintain depth state and emit metrics.
#[derive(Default)]
pub struct OrderBookStore {
    books: HashMap<TickerId, OrderBook>,
}

impl OrderBookStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Get or create an order book for a ticker.
    pub fn get_or_create(&mut self, ticker_id: TickerId) -> &mut OrderBook {
        self.books.entry(ticker_id).or_default()
    }

    /// Get the order book for a ticker (read-only).
    pub fn get(&self, ticker_id: &TickerId) -> Option<&OrderBook> {
        self.books.get(ticker_id)
    }

    /// Compute depth metrics for a ticker. Returns None if no book or no data.
    pub fn metrics(&self, ticker_id: &TickerId) -> Option<DepthMetrics> {
        self.books.get(ticker_id)
            .filter(|b| b.has_data())
            .map(DepthMetrics::from_book)
    }

    /// Number of tickers with active order books.
    pub fn len(&self) -> usize {
        self.books.len()
    }

    /// Whether the store is empty.
    pub fn is_empty(&self) -> bool {
        self.books.is_empty()
    }

    /// Clear all order books (on disconnect).
    pub fn clear(&mut self) {
        self.books.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_order_book_default_empty() {
        let book = OrderBook::default();
        assert!(!book.has_data());
        assert_eq!(book.update_count, 0);
    }

    #[test]
    fn test_apply_insert_bid_ask() {
        let mut book = OrderBook::default();
        // Insert bid at position 0: side=1 (bid), operation=0 (insert)
        book.apply_update(0, 0, 1, 100.50, 200.0, 1_000_000);
        // Insert ask at position 0: side=0 (ask), operation=0 (insert)
        book.apply_update(0, 0, 0, 100.55, 150.0, 1_000_001);

        assert!(book.has_data());
        assert_eq!(book.bids[0].price, 100.50);
        assert_eq!(book.bids[0].size, 200.0);
        assert_eq!(book.asks[0].price, 100.55);
        assert_eq!(book.asks[0].size, 150.0);
        assert_eq!(book.update_count, 2);
    }

    #[test]
    fn test_apply_update_replaces_level() {
        let mut book = OrderBook::default();
        book.apply_update(0, 0, 1, 100.50, 200.0, 1_000_000);
        // Update bid at position 0: operation=1 (update)
        book.apply_update(0, 1, 1, 100.50, 300.0, 1_000_002);

        assert_eq!(book.bids[0].size, 300.0);
    }

    #[test]
    fn test_apply_delete_clears_level() {
        let mut book = OrderBook::default();
        book.apply_update(0, 0, 1, 100.50, 200.0, 1_000_000);
        // Delete bid at position 0: operation=2 (delete)
        book.apply_update(0, 2, 1, 0.0, 0.0, 1_000_003);

        assert_eq!(book.bids[0].price, 0.0);
        assert_eq!(book.bids[0].size, 0.0);
    }

    #[test]
    fn test_out_of_range_position_ignored() {
        let mut book = OrderBook::default();
        book.apply_update(5, 0, 1, 100.50, 200.0, 1_000_000);
        assert_eq!(book.update_count, 0); // Should not increment
    }

    #[test]
    fn test_depth_metrics_basic() {
        let mut book = OrderBook::default();
        // 3 bid levels
        book.apply_update(0, 0, 1, 100.00, 100.0, 1);
        book.apply_update(1, 0, 1, 99.95, 200.0, 2);
        book.apply_update(2, 0, 1, 99.90, 50.0, 3);
        // 3 ask levels
        book.apply_update(0, 0, 0, 100.05, 80.0, 4);
        book.apply_update(1, 0, 0, 100.10, 300.0, 5);
        book.apply_update(2, 0, 0, 100.15, 60.0, 6);

        let metrics = DepthMetrics::from_book(&book);

        assert_eq!(metrics.total_bid_depth, 350.0);
        assert_eq!(metrics.total_ask_depth, 440.0);
        // depth_imbalance = (350 - 440) / (350 + 440) = -90/790 ≈ -0.1139
        assert!((metrics.depth_imbalance - (-90.0 / 790.0)).abs() < 1e-6);
        // bid wall = position 1 (size=200)
        assert_eq!(metrics.bid_wall_price, 99.95);
        // ask wall = position 1 (size=300)
        assert_eq!(metrics.ask_wall_price, 100.10);
        // spread_depth_1 = 100.05 - 100.00 = 0.05
        assert!((metrics.spread_depth_1 - 0.05).abs() < 1e-10);
        // spread_depth_5 = 100.15 - 99.90 = 0.25 (outermost populated levels)
        assert!((metrics.spread_depth_5 - 0.25).abs() < 1e-10);
        // book_pressure should be negative (more ask depth near mid)
        assert!(metrics.book_pressure < 0.0);
    }

    #[test]
    fn test_depth_metrics_symmetric_book() {
        let mut book = OrderBook::default();
        book.apply_update(0, 0, 1, 100.00, 100.0, 1);
        book.apply_update(0, 0, 0, 100.02, 100.0, 2);

        let metrics = DepthMetrics::from_book(&book);
        assert!((metrics.depth_imbalance - 0.0).abs() < 1e-10);
    }

    #[test]
    fn test_order_book_store_get_or_create() {
        let mut store = OrderBookStore::new();
        let tid = TickerId(42);

        let book = store.get_or_create(tid);
        book.apply_update(0, 0, 1, 50.0, 100.0, 1);
        book.apply_update(0, 0, 0, 50.1, 100.0, 2);

        assert_eq!(store.len(), 1);
        let metrics = store.metrics(&tid);
        assert!(metrics.is_some());
        assert_eq!(metrics.as_ref().map(|m| m.total_bid_depth), Some(100.0));
    }

    #[test]
    fn test_order_book_store_clear_on_disconnect() {
        let mut store = OrderBookStore::new();
        store.get_or_create(TickerId(1));
        store.get_or_create(TickerId(2));
        assert_eq!(store.len(), 2);

        store.clear();
        assert_eq!(store.len(), 0);
    }

    #[test]
    fn test_metrics_none_for_empty_book() {
        let store = OrderBookStore::new();
        assert!(store.metrics(&TickerId(99)).is_none());
    }
}
