//! P5-D: Hayashi-Yoshida Covariance Estimator (Hayashi & Yoshida 2005).
//! Computes realized covariance from asynchronous tick data.
//! Standard covariance requires synchronized timestamps; H-Y handles
//! ticks arriving at different times across instruments.
//! Feeds into: Kelly factor 4 (correlation penalty), InfiniteChandelier
//! correlation multiplier, and CrossTimezone sentiment.

use std::collections::{HashMap, VecDeque};

use crate::types::TickerId;

/// A single tick observation: (timestamp_ns, log_return).
#[derive(Clone, Debug)]
struct TickReturn {
    timestamp_ns: u64,
    log_return: f64,
}

/// Per-ticker return series for H-Y computation.
struct ReturnSeries {
    returns: VecDeque<TickReturn>,
    last_price: f64,
    max_returns: usize,
}

impl ReturnSeries {
    fn new(max_returns: usize) -> Self {
        Self {
            returns: VecDeque::with_capacity(max_returns),
            last_price: 0.0,
            max_returns,
        }
    }

    /// Record a new price tick. Returns the log return if we have a previous price.
    fn record(&mut self, price: f64, timestamp_ns: u64) -> Option<f64> {
        if price <= 0.0 || !price.is_finite() {
            return None;
        }
        if self.last_price <= 0.0 {
            self.last_price = price;
            return None;
        }
        let log_return = (price / self.last_price).ln();
        self.last_price = price;

        if self.returns.len() >= self.max_returns {
            self.returns.pop_front();
        }
        self.returns.push_back(TickReturn {
            timestamp_ns,
            log_return,
        });
        Some(log_return)
    }

    fn len(&self) -> usize {
        self.returns.len()
    }
}

/// Hayashi-Yoshida covariance estimator for multiple asset pairs.
pub struct HayashiYoshidaEngine {
    /// Per-ticker return series.
    series: HashMap<TickerId, ReturnSeries>,
    /// Max returns per ticker.
    max_returns: usize,
    /// Cached pairwise correlations (recomputed periodically).
    cached_correlations: HashMap<(TickerId, TickerId), f64>,
    /// Ticks since last recomputation.
    ticks_since_recompute: u32,
    /// Recompute interval (ticks).
    recompute_interval: u32,
}

impl HayashiYoshidaEngine {
    pub fn new(max_returns: usize, recompute_interval: u32) -> Self {
        Self {
            series: HashMap::new(),
            max_returns,
            cached_correlations: HashMap::new(),
            ticks_since_recompute: 0,
            recompute_interval,
        }
    }

    /// Record a price tick for a ticker.
    pub fn record_tick(&mut self, ticker_id: TickerId, price: f64, timestamp_ns: u64) {
        self.series
            .entry(ticker_id)
            .or_insert_with(|| ReturnSeries::new(self.max_returns))
            .record(price, timestamp_ns);

        self.ticks_since_recompute += 1;
        if self.ticks_since_recompute >= self.recompute_interval {
            self.recompute_all();
            self.ticks_since_recompute = 0;
        }
    }

    /// Get the cached correlation between two tickers.
    /// Returns 0.0 if not yet computed or insufficient data.
    pub fn correlation(&self, a: TickerId, b: TickerId) -> f64 {
        let key = if a.0 <= b.0 { (a, b) } else { (b, a) };
        self.cached_correlations.get(&key).copied().unwrap_or(0.0)
    }

    /// Get the average correlation of a ticker with all others.
    pub fn avg_correlation(&self, ticker_id: TickerId) -> f64 {
        let mut sum = 0.0;
        let mut count = 0u32;
        for (&(a, b), &corr) in &self.cached_correlations {
            if a == ticker_id || b == ticker_id {
                sum += corr;
                count += 1;
            }
        }
        if count == 0 {
            0.0
        } else {
            sum / count as f64
        }
    }

    /// Number of tickers being tracked.
    pub fn ticker_count(&self) -> usize {
        self.series.len()
    }

    /// Number of cached pairwise correlations.
    pub fn pair_count(&self) -> usize {
        self.cached_correlations.len()
    }

    /// Recompute all pairwise H-Y correlations.
    fn recompute_all(&mut self) {
        let tids: Vec<TickerId> = self.series.keys().copied().collect();
        let n = tids.len();

        for i in 0..n {
            for j in (i + 1)..n {
                let a = tids[i];
                let b = tids[j];
                if let Some(corr) = self.compute_hy_correlation(a, b) {
                    let key = if a.0 <= b.0 { (a, b) } else { (b, a) };
                    self.cached_correlations.insert(key, corr);
                }
            }
        }
    }

    /// Compute Hayashi-Yoshida covariance between two tickers,
    /// then normalize to correlation.
    fn compute_hy_correlation(&self, a: TickerId, b: TickerId) -> Option<f64> {
        let series_a = self.series.get(&a)?;
        let series_b = self.series.get(&b)?;

        if series_a.len() < 20 || series_b.len() < 20 {
            return None; // Insufficient data
        }

        let returns_a = &series_a.returns;
        let returns_b = &series_b.returns;

        // H-Y estimator: Σ_ij r_i^a * r_j^b * 1{time intervals overlap}
        // Two return intervals overlap if max(start_i, start_j) < min(end_i, end_j).
        let mut hy_cov = 0.0;
        let mut var_a = 0.0;
        let mut var_b = 0.0;

        // Build interval endpoints for series A
        let intervals_a: Vec<(u64, u64, f64)> = returns_a
            .iter()
            .zip(returns_a.iter().skip(1))
            .map(|(prev, curr)| (prev.timestamp_ns, curr.timestamp_ns, curr.log_return))
            .collect();

        let intervals_b: Vec<(u64, u64, f64)> = returns_b
            .iter()
            .zip(returns_b.iter().skip(1))
            .map(|(prev, curr)| (prev.timestamp_ns, curr.timestamp_ns, curr.log_return))
            .collect();

        if intervals_a.is_empty() || intervals_b.is_empty() {
            return None;
        }

        // O(n*m) with sliding window on sorted timestamps.
        // j_start tracks the first B interval that could overlap with current A interval.
        let mut j_lo = 0;
        for &(a_start, a_end, r_a) in &intervals_a {
            var_a += r_a * r_a;
            // Advance j_lo past B intervals that end before A starts
            while j_lo < intervals_b.len() && intervals_b[j_lo].1 <= a_start {
                j_lo += 1;
            }
            // Scan from j_lo forward for overlapping B intervals
            for &(b_start, _b_end, r_b) in intervals_b.iter().skip(j_lo) {
                if b_start >= a_end {
                    break; // No more overlaps possible (sorted)
                }
                // Overlap exists
                hy_cov += r_a * r_b;
            }
        }

        for &(_, _, r_b) in &intervals_b {
            var_b += r_b * r_b;
        }

        if var_a <= 0.0 || var_b <= 0.0 {
            return None;
        }

        // Correlation = cov / sqrt(var_a * var_b)
        let correlation = hy_cov / (var_a.sqrt() * var_b.sqrt());
        Some(correlation.clamp(-1.0, 1.0))
    }
}

impl Default for HayashiYoshidaEngine {
    fn default() -> Self {
        Self::new(1000, 500) // 1000 returns, recompute every 500 ticks
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_return_series_basic() {
        let mut series = ReturnSeries::new(100);
        assert!(series.record(100.0, 1_000_000_000).is_none()); // First tick, no return
        let ret = series.record(101.0, 2_000_000_000);
        assert!(ret.is_some());
        let r = ret.expect("return");
        assert!((r - (101.0_f64 / 100.0).ln()).abs() < 1e-10);
    }

    #[test]
    fn test_hy_engine_cold_start() {
        let engine = HayashiYoshidaEngine::new(100, 50);
        assert_eq!(engine.ticker_count(), 0);
        assert_eq!(engine.correlation(TickerId(0), TickerId(1)), 0.0);
    }

    #[test]
    fn test_hy_engine_records_ticks() {
        let mut engine = HayashiYoshidaEngine::new(100, 50);
        for i in 0..30 {
            engine.record_tick(TickerId(0), 100.0 + i as f64 * 0.1, i * 1_000_000_000);
            engine.record_tick(TickerId(1), 50.0 + i as f64 * 0.05, i * 1_000_000_000);
        }
        assert_eq!(engine.ticker_count(), 2);
    }

    #[test]
    fn test_hy_perfectly_correlated() {
        let mut engine = HayashiYoshidaEngine::new(1000, 50);
        // Two tickers moving in sync
        for i in 0..100 {
            let t = (i + 1) as u64 * 1_000_000_000;
            let price = 100.0 + (i as f64 * 0.1).sin() * 5.0;
            engine.record_tick(TickerId(0), price, t);
            engine.record_tick(TickerId(1), price * 2.0, t); // Same direction, different scale
        }
        // Force recompute
        engine.recompute_all();
        let corr = engine.correlation(TickerId(0), TickerId(1));
        // Should be highly correlated (close to 1.0)
        assert!(
            corr > 0.8,
            "Perfectly correlated tickers should have high correlation, got {corr}"
        );
    }

    #[test]
    fn test_hy_buffer_bounded() {
        let mut series = ReturnSeries::new(10);
        for i in 0..20 {
            series.record(100.0 + i as f64, i as u64 * 1_000_000_000);
        }
        assert!(series.len() <= 10);
    }

    #[test]
    fn test_hy_avg_correlation() {
        let engine = HayashiYoshidaEngine::new(100, 50);
        // No data → 0.0
        assert_eq!(engine.avg_correlation(TickerId(0)), 0.0);
    }

    #[test]
    fn test_hy_ignores_bad_prices() {
        let mut series = ReturnSeries::new(100);
        assert!(series.record(0.0, 1_000_000_000).is_none());
        assert!(series.record(-5.0, 2_000_000_000).is_none());
        assert!(series.record(f64::NAN, 3_000_000_000).is_none());
        assert_eq!(series.len(), 0);
    }
}
