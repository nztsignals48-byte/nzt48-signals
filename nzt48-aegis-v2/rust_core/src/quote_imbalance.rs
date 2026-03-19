//! Quote Imbalance Circuit Breaker — detects spoofed/gapped quotes
//! by comparing live spread width against a rolling median baseline.

use std::collections::{HashMap, VecDeque};

use crate::types::TickerId;

/// Maximum snapshots retained per ticker.
const MAX_WINDOW: usize = 100;
/// Number of recent spreads used to compute the median baseline.
const MEDIAN_WINDOW: usize = 20;
/// A live spread wider than `SPOOF_MULTIPLIER × median` triggers the spoof flag.
const SPOOF_MULTIPLIER: f64 = 10.0;
/// Spread is considered normalized when last N spreads are all within this multiplier of median.
const NORMALIZE_MULTIPLIER: f64 = 3.0;
/// Number of consecutive spreads that must be within `NORMALIZE_MULTIPLIER` for normalization.
const NORMALIZE_COUNT: usize = 5;

/// A single point-in-time bid/ask observation.
#[derive(Debug, Clone, Copy)]
pub struct SpreadSnapshot {
    pub bid: f64,
    pub ask: f64,
    pub timestamp_ns: u64,
    pub spread_pct: f64,
}

/// Per-ticker bookkeeping.
#[derive(Debug, Default)]
struct TickerState {
    window: VecDeque<SpreadSnapshot>,
    drop_count: u64,
}

/// Detects anomalous spread widening that may indicate spoofing or
/// liquidity withdrawal, and gates order flow until spreads normalise.
#[derive(Debug, Default)]
pub struct QuoteImbalanceDetector {
    state: HashMap<TickerId, TickerState>,
}

impl QuoteImbalanceDetector {
    pub fn new() -> Self {
        Self::default()
    }

    /// Record a new quote and automatically increment the drop counter if spoofed.
    pub fn record_quote(&mut self, ticker_id: TickerId, bid: f64, ask: f64, now_ns: u64) {
        let mid = (bid + ask) / 2.0;
        let spread_pct = if mid.abs() < f64::EPSILON {
            0.0
        } else {
            (ask - bid) / mid * 100.0
        };

        let snap = SpreadSnapshot {
            bid,
            ask,
            timestamp_ns: now_ns,
            spread_pct,
        };

        let ts = self.state.entry(ticker_id).or_default();
        ts.window.push_back(snap);
        if ts.window.len() > MAX_WINDOW {
            ts.window.pop_front();
        }

        // Auto-count drops.
        if self.is_spoofed_inner(ticker_id) {
            self.state.entry(ticker_id).and_modify(|ts| ts.drop_count += 1);
        }
    }

    /// Returns `true` if the latest spread is ≥ 10× the 20-spread median.
    pub fn is_spoofed(&self, ticker_id: TickerId) -> bool {
        self.is_spoofed_inner(ticker_id)
    }

    /// Total spoofed-quote detections for this ticker since last reset.
    pub fn drop_count(&self, ticker_id: TickerId) -> u64 {
        self.state.get(&ticker_id).map_or(0, |ts| ts.drop_count)
    }

    /// Reset the drop counter (call after spread normalises and cooldown expires).
    pub fn reset_drops(&mut self, ticker_id: TickerId) {
        if let Some(ts) = self.state.get_mut(&ticker_id) {
            ts.drop_count = 0;
        }
    }

    /// Returns `true` if the last 5 spreads are all within 3× of the 20-spread median.
    pub fn is_normalized(&self, ticker_id: TickerId) -> bool {
        let ts = match self.state.get(&ticker_id) {
            Some(ts) => ts,
            None => return false,
        };
        if ts.window.len() < NORMALIZE_COUNT {
            return false;
        }
        let median = match Self::median_spread(&ts.window, MEDIAN_WINDOW) {
            Some(m) => m,
            None => return false,
        };
        let threshold = median * NORMALIZE_MULTIPLIER;
        ts.window
            .iter()
            .rev()
            .take(NORMALIZE_COUNT)
            .all(|s| s.spread_pct <= threshold)
    }

    // --- private helpers ---

    fn is_spoofed_inner(&self, ticker_id: TickerId) -> bool {
        let ts = match self.state.get(&ticker_id) {
            Some(ts) => ts,
            None => return false,
        };
        // Need at least MEDIAN_WINDOW samples to build a stable baseline.
        // Without this, early volatile spreads cause false spoof detections.
        if ts.window.len() < MEDIAN_WINDOW {
            return false;
        }
        let latest = match ts.window.back() {
            Some(s) => s,
            None => return false,
        };
        let median = match Self::median_spread(&ts.window, MEDIAN_WINDOW) {
            Some(m) if m > f64::EPSILON => m,
            _ => return false,
        };
        latest.spread_pct >= median * SPOOF_MULTIPLIER
    }

    /// Compute the median spread_pct of the last `n` entries in `window`.
    fn median_spread(window: &VecDeque<SpreadSnapshot>, n: usize) -> Option<f64> {
        if window.is_empty() {
            return None;
        }
        let take = n.min(window.len());
        let mut vals: Vec<f64> = window.iter().rev().take(take).map(|s| s.spread_pct).collect();
        vals.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let mid = vals.len() / 2;
        if vals.len().is_multiple_of(2) {
            Some((vals[mid - 1] + vals[mid]) / 2.0)
        } else {
            Some(vals[mid])
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tid(id: u32) -> TickerId {
        TickerId(id)
    }

    /// Helper: record N quotes with a fixed spread.
    fn fill_baseline(det: &mut QuoteImbalanceDetector, ticker: TickerId, n: usize, bid: f64, ask: f64) {
        for i in 0..n {
            det.record_quote(ticker, bid, ask, i as u64 * 1_000_000);
        }
    }

    #[test]
    fn normal_spread_not_spoofed() {
        let mut det = QuoteImbalanceDetector::new();
        fill_baseline(&mut det, tid(1), 25, 100.0, 100.10);
        assert!(!det.is_spoofed(tid(1)));
        assert_eq!(det.drop_count(tid(1)), 0);
    }

    #[test]
    fn wide_spread_detected_as_spoofed() {
        let mut det = QuoteImbalanceDetector::new();
        // Build a baseline of ~0.1% spread.
        fill_baseline(&mut det, tid(1), 20, 100.0, 100.10);
        // Now inject a quote with ~10% spread (100× wider than 0.1%).
        det.record_quote(tid(1), 95.0, 105.0, 100_000_000);
        assert!(det.is_spoofed(tid(1)));
        assert!(det.drop_count(tid(1)) >= 1);
    }

    #[test]
    fn borderline_not_spoofed() {
        let mut det = QuoteImbalanceDetector::new();
        // Baseline: mid=100.05, spread=0.10, spread_pct ≈ 0.0999%
        fill_baseline(&mut det, tid(1), 20, 100.0, 100.10);
        // 9× wider spread should NOT trigger (threshold is 10×).
        // 9 × 0.10 = 0.90 spread → bid=99.55, ask=100.45, mid=100, spread_pct≈0.9%
        det.record_quote(tid(1), 99.55, 100.45, 100_000_000);
        assert!(!det.is_spoofed(tid(1)));
    }

    #[test]
    fn drop_count_increments_and_resets() {
        let mut det = QuoteImbalanceDetector::new();
        fill_baseline(&mut det, tid(2), 20, 50.0, 50.05);
        // Two spoofed quotes.
        det.record_quote(tid(2), 40.0, 60.0, 200_000_000);
        det.record_quote(tid(2), 40.0, 60.0, 300_000_000);
        assert!(det.drop_count(tid(2)) >= 2);
        det.reset_drops(tid(2));
        assert_eq!(det.drop_count(tid(2)), 0);
    }

    #[test]
    fn is_normalized_after_recovery() {
        let mut det = QuoteImbalanceDetector::new();
        fill_baseline(&mut det, tid(3), 20, 100.0, 100.10);
        // Spike.
        det.record_quote(tid(3), 90.0, 110.0, 100_000_000);
        assert!(!det.is_normalized(tid(3)));
        // 5 normal quotes to recover (within 3× of median).
        for i in 0..5 {
            det.record_quote(tid(3), 100.0, 100.20, (200 + i) as u64 * 1_000_000);
        }
        assert!(det.is_normalized(tid(3)));
    }

    #[test]
    fn unknown_ticker_safe_defaults() {
        let det = QuoteImbalanceDetector::new();
        assert!(!det.is_spoofed(tid(999)));
        assert!(!det.is_normalized(tid(999)));
        assert_eq!(det.drop_count(tid(999)), 0);
    }

    #[test]
    fn window_caps_at_100() {
        let mut det = QuoteImbalanceDetector::new();
        fill_baseline(&mut det, tid(4), 150, 100.0, 100.10);
        let ts = det.state.get(&tid(4)).expect("ticker should exist");
        assert_eq!(ts.window.len(), MAX_WINDOW);
    }

    #[test]
    fn multiple_tickers_independent() {
        let mut det = QuoteImbalanceDetector::new();
        fill_baseline(&mut det, tid(10), 20, 100.0, 100.10);
        fill_baseline(&mut det, tid(11), 20, 200.0, 200.20);
        // Spike only ticker 10.
        det.record_quote(tid(10), 80.0, 120.0, 999_000_000);
        assert!(det.is_spoofed(tid(10)));
        assert!(!det.is_spoofed(tid(11)));
    }
}
