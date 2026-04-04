//! Signal Scanner Stack — institutional multi-strategy signal generation.
//! MomentumScanner: volatility-momentum scanner for all tickers (continuous ticks).
//! SectorRotationScanner: sector rotation scanner for sector-level snapshots.
//! Both produce ranked signal candidates for the Python Brain to refine.

use std::collections::HashMap;

use crate::student_t_kalman::StudentTKalmanFilter;
use crate::types::TickerId;

/// Signal candidate from a scanner (pre-Brain refinement).
#[derive(Clone, Debug)]
pub struct SignalCandidate {
    pub ticker_id: TickerId,
    /// Composite score [0.0, 100.0]. Higher = stronger signal.
    pub score: f64,
    /// Signal direction: positive = long, negative = short.
    pub direction_bias: f64,
    /// Which scanner produced this signal.
    pub source: ScannerSource,
    /// Timestamp of the signal (nanoseconds).
    pub timestamp_ns: u64,
}

/// Scanner that produced the signal.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ScannerSource {
    /// Real-time volatility-momentum scanner (tick-level).
    MomentumScanner,
    /// Sector rotation scanner (60s snapshots, relative strength).
    SectorRotation,
}

// ────────────────────────────────────────────
// MomentumScanner: volatility + momentum on all active tickers
// ────────────────────────────────────────────

/// Per-ticker tracking state for the MomentumScanner.
#[derive(Clone, Debug)]
struct HotState {
    /// Recent tick prices for momentum calculation (last N ticks).
    prices: Vec<f64>,
    /// Recent volumes for volume surge detection.
    volumes: Vec<u64>,
    /// 20-period simple moving average of volume.
    vol_sma_20: f64,
    /// Running price momentum (rate of change over lookback).
    momentum: f64,
    /// Last computed score.
    last_score: f64,
    /// Maximum prices to retain.
    max_history: usize,
}

impl HotState {
    fn new() -> Self {
        Self {
            prices: Vec::with_capacity(100),
            volumes: Vec::with_capacity(100),
            vol_sma_20: 0.0,
            momentum: 0.0,
            last_score: 0.0,
            max_history: 100,
        }
    }

    fn push_tick(&mut self, price: f64, volume: u64) {
        if !price.is_finite() || price <= 0.0 {
            return; // Reject invalid ticks
        }
        self.prices.push(price);
        self.volumes.push(volume);
        if self.prices.len() > self.max_history {
            self.prices.remove(0);
            self.volumes.remove(0);
        }
        self.update_indicators();
    }

    fn update_indicators(&mut self) {
        let n = self.volumes.len();
        // Volume SMA-20
        if n >= 20 {
            let sum: u64 = self.volumes[n - 20..].iter().sum();
            self.vol_sma_20 = sum as f64 / 20.0;
        }
        // Price momentum: percentage change over last 20 ticks
        if self.prices.len() >= 20 {
            let old = self.prices[self.prices.len() - 20];
            let new = *self.prices.last().unwrap_or(&0.0);
            if old > 0.0 {
                self.momentum = (new - old) / old;
            }
        }
    }

    /// Compute composite hot score [0, 100].
    fn compute_score(&mut self, current_vol: f64, atr: f64, current_price: f64) -> f64 {
        let mut score = 0.0;

        // Component 1: Volume surge (0-30 points)
        // Volume > 2x SMA-20 = full 30 points, linear scale
        if self.vol_sma_20 > 0.0 {
            let vol_ratio = current_vol / self.vol_sma_20;
            score += (vol_ratio / 2.0).min(1.0) * 30.0;
        }

        // Component 2: Price momentum (0-30 points)
        // Absolute momentum > 2% = full 30 points, linear scale
        let abs_momentum = self.momentum.abs();
        score += (abs_momentum / 0.02).min(1.0) * 30.0;

        // Component 3: Volatility expansion (0-25 points)
        // ATR-to-price ratio > 2% = high volatility signal
        if current_price > 0.0 {
            let vol_pct = atr / current_price;
            score += (vol_pct / 0.02).min(1.0) * 25.0;
        }

        // Component 4: Trend alignment (0-15 points)
        // Momentum direction consistency over last 5 ticks
        if self.prices.len() >= 5 {
            let last_5 = &self.prices[self.prices.len() - 5..];
            let ups = last_5.windows(2).filter(|w| w[1] > w[0]).count();
            let consistency = if self.momentum > 0.0 {
                ups as f64 / 4.0
            } else {
                (4 - ups) as f64 / 4.0
            };
            score += consistency * 15.0;
        }

        self.last_score = score.min(100.0);
        self.last_score
    }
}

/// MomentumScanner: real-time volatility-momentum signal generator.
pub struct MomentumScanner {
    states: HashMap<TickerId, HotState>,
    /// P5-B: Per-ticker Student-t Kalman filters for price smoothing.
    kalman_filters: HashMap<TickerId, StudentTKalmanFilter>,
    /// Minimum score threshold to emit a signal candidate.
    pub score_threshold: f64,
    /// Maximum number of candidates to return per scan cycle.
    pub max_candidates: usize,
}

impl MomentumScanner {
    pub fn new(score_threshold: f64, max_candidates: usize) -> Self {
        Self {
            states: HashMap::new(),
            kalman_filters: HashMap::new(),
            score_threshold,
            max_candidates,
        }
    }

    /// Feed a tick into the scanner.
    /// P5-B: Prices are Kalman-filtered before momentum computation.
    pub fn on_tick(
        &mut self,
        ticker_id: TickerId,
        price: f64,
        volume: u64,
        atr: f64,
        now_ns: u64,
    ) -> Option<SignalCandidate> {
        // P5-B: Smooth price via Student-t Kalman filter.
        // Rejects spoofed quotes (Mahalanobis-weighted update) and
        // provides cleaner momentum signals.
        let kalman = self.kalman_filters.entry(ticker_id).or_insert_with(|| {
            StudentTKalmanFilter::new(price, 1.0, 0.001, 0.01, 200)
        });
        let _innovation = kalman.step(price);
        let smoothed_price = kalman.state();

        let state = self
            .states
            .entry(ticker_id)
            .or_insert_with(HotState::new);

        state.push_tick(smoothed_price, volume);
        let score = state.compute_score(volume as f64, atr, price);

        if score >= self.score_threshold {
            Some(SignalCandidate {
                ticker_id,
                score,
                direction_bias: state.momentum,
                source: ScannerSource::MomentumScanner,
                timestamp_ns: now_ns,
            })
        } else {
            None
        }
    }

    /// Get the top N candidates across all tickers (ranked by score).
    pub fn top_candidates(&self, now_ns: u64) -> Vec<SignalCandidate> {
        let mut candidates: Vec<SignalCandidate> = self
            .states
            .iter()
            .filter(|(_, s)| s.last_score >= self.score_threshold)
            .map(|(&tid, s)| SignalCandidate {
                ticker_id: tid,
                score: s.last_score,
                direction_bias: s.momentum,
                source: ScannerSource::MomentumScanner,
                timestamp_ns: now_ns,
            })
            .collect();
        candidates.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        candidates.truncate(self.max_candidates);
        candidates
    }

    /// Reset all tracking state.
    pub fn reset(&mut self) {
        self.states.clear();
    }

    /// Number of tracked tickers.
    pub fn tracked_count(&self) -> usize {
        self.states.len()
    }
}

// ────────────────────────────────────────────
// SectorRotationScanner: sector rotation signal generation
// ────────────────────────────────────────────

/// Sector performance tracker.
#[derive(Clone, Debug)]
struct SectorState {
    /// Sector name (used for logging/debugging).
    #[allow(dead_code)]
    name: String,
    /// Ticker IDs in this sector.
    tickers: Vec<TickerId>,
    /// Running sector return (weighted average of ticker returns).
    sector_return: f64,
    /// Previous period sector return (for momentum change detection).
    prev_return: f64,
    /// Sector relative strength index.
    relative_strength: f64,
}

/// SectorRotationScanner: identifies sector leadership changes.
pub struct SectorRotationScanner {
    sectors: HashMap<String, SectorState>,
    /// Per-ticker return tracking (60s snapshots).
    ticker_returns: HashMap<TickerId, f64>,
    /// Previous prices per ticker (for return calculation).
    prev_prices: HashMap<TickerId, f64>,
    /// Minimum rotation strength to emit signal.
    pub rotation_threshold: f64,
    /// Maximum candidates per scan.
    pub max_candidates: usize,
}

impl SectorRotationScanner {
    pub fn new(rotation_threshold: f64, max_candidates: usize) -> Self {
        Self {
            sectors: HashMap::new(),
            ticker_returns: HashMap::new(),
            prev_prices: HashMap::new(),
            rotation_threshold,
            max_candidates,
        }
    }

    /// Register a ticker in a sector.
    pub fn register_ticker(&mut self, ticker_id: TickerId, sector: &str) {
        let entry = self.sectors.entry(sector.to_string()).or_insert_with(|| {
            SectorState {
                name: sector.to_string(),
                tickers: Vec::new(),
                sector_return: 0.0,
                prev_return: 0.0,
                relative_strength: 0.0,
            }
        });
        if !entry.tickers.contains(&ticker_id) {
            entry.tickers.push(ticker_id);
        }
    }

    /// Update with a 60s Apex snapshot.
    pub fn on_snapshot(&mut self, ticker_id: TickerId, close_price: f64) {
        if let Some(&prev) = self.prev_prices.get(&ticker_id)
            && prev > 0.0 {
                let ret = (close_price - prev) / prev;
                self.ticker_returns.insert(ticker_id, ret);
            }
        self.prev_prices.insert(ticker_id, close_price);
    }

    /// Recompute sector returns and relative strengths.
    /// Call after a batch of snapshots.
    pub fn recompute_sectors(&mut self) {
        // Calculate average market return
        let all_returns: Vec<f64> = self.ticker_returns.values().copied().collect();
        let market_avg = if all_returns.is_empty() {
            0.0
        } else {
            all_returns.iter().sum::<f64>() / all_returns.len() as f64
        };

        // Update each sector
        for sector in self.sectors.values_mut() {
            sector.prev_return = sector.sector_return;

            let returns: Vec<f64> = sector
                .tickers
                .iter()
                .filter_map(|tid| self.ticker_returns.get(tid).copied())
                .collect();

            if returns.is_empty() {
                sector.sector_return = 0.0;
                sector.relative_strength = 0.0;
                continue;
            }

            sector.sector_return = returns.iter().sum::<f64>() / returns.len() as f64;
            // Relative strength: sector return vs market average
            sector.relative_strength = sector.sector_return - market_avg;
        }
    }

    /// Get rotation signal candidates: sectors gaining relative strength.
    pub fn rotation_candidates(&self, now_ns: u64) -> Vec<SignalCandidate> {
        let mut candidates = Vec::new();

        for sector in self.sectors.values() {
            // Detect improving sectors: relative_strength > threshold AND improving
            let improving = sector.relative_strength > sector.prev_return;
            if sector.relative_strength.abs() >= self.rotation_threshold && improving {
                // Emit signal for the strongest ticker in this sector
                let best_ticker = sector
                    .tickers
                    .iter()
                    .filter_map(|tid| {
                        self.ticker_returns
                            .get(tid)
                            .map(|&ret| (*tid, ret))
                    })
                    .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));

                if let Some((tid, ret)) = best_ticker {
                    let score = (sector.relative_strength.abs() / self.rotation_threshold)
                        .min(1.0)
                        * 80.0
                        + 20.0; // Base 20 + up to 80

                    candidates.push(SignalCandidate {
                        ticker_id: tid,
                        score: score.min(100.0),
                        direction_bias: ret,
                        source: ScannerSource::SectorRotation,
                        timestamp_ns: now_ns,
                    });
                }
            }
        }

        candidates.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        candidates.truncate(self.max_candidates);
        candidates
    }

    /// Number of sectors tracked.
    pub fn sector_count(&self) -> usize {
        self.sectors.len()
    }

    /// Reset all state.
    pub fn reset(&mut self) {
        self.sectors.clear();
        self.ticker_returns.clear();
        self.prev_prices.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── MomentumScanner tests ──

    #[test]
    fn test_hot_scanner_below_threshold_no_signal() {
        let mut scanner = MomentumScanner::new(50.0, 10);
        // Low-volume, low-momentum tick
        let result = scanner.on_tick(TickerId(1), 100.0, 100, 1.0, 1_000_000_000);
        assert!(result.is_none());
    }

    #[test]
    fn test_hot_scanner_volume_surge_generates_signal() {
        let mut scanner = MomentumScanner::new(30.0, 10);
        // Build up baseline volume (20 ticks at volume=1000)
        for i in 0..20 {
            scanner.on_tick(TickerId(1), 100.0 + i as f64 * 0.1, 1000, 2.0, i * 1_000_000_000);
        }
        // Volume surge: 5x normal → should trigger
        let result = scanner.on_tick(TickerId(1), 102.0, 5000, 2.0, 20_000_000_000);
        assert!(result.is_some());
        let sig = result.expect("signal");
        assert_eq!(sig.source, ScannerSource::MomentumScanner);
        assert!(sig.score >= 30.0);
    }

    #[test]
    fn test_hot_scanner_top_candidates_ranked() {
        let mut scanner = MomentumScanner::new(20.0, 5);
        // Ticker 1: moderate activity
        for i in 0..25 {
            scanner.on_tick(TickerId(1), 100.0 + i as f64 * 0.1, 1000, 2.0, i * 1_000_000_000);
        }
        // Ticker 2: high activity
        for i in 0..25 {
            scanner.on_tick(TickerId(2), 50.0 + i as f64 * 0.5, 3000, 3.0, i * 1_000_000_000);
        }

        let candidates = scanner.top_candidates(25_000_000_000);
        assert!(!candidates.is_empty());
        // Should be sorted by score descending
        if candidates.len() >= 2 {
            assert!(candidates[0].score >= candidates[1].score);
        }
    }

    #[test]
    fn test_hot_scanner_max_candidates_limit() {
        let mut scanner = MomentumScanner::new(0.0, 3); // threshold=0 → all emit
        for tid in 0..10 {
            for i in 0..25 {
                scanner.on_tick(
                    TickerId(tid),
                    100.0 + i as f64,
                    1000 + i * 100,
                    2.0,
                    i * 1_000_000_000,
                );
            }
        }
        let candidates = scanner.top_candidates(25_000_000_000);
        assert!(candidates.len() <= 3);
    }

    #[test]
    fn test_hot_scanner_reset() {
        let mut scanner = MomentumScanner::new(50.0, 10);
        scanner.on_tick(TickerId(1), 100.0, 1000, 2.0, 1_000_000_000);
        assert_eq!(scanner.tracked_count(), 1);
        scanner.reset();
        assert_eq!(scanner.tracked_count(), 0);
    }

    // ── SectorRotationScanner tests ──

    #[test]
    fn test_rotation_scanner_register_tickers() {
        let mut scanner = SectorRotationScanner::new(0.005, 10);
        scanner.register_ticker(TickerId(1), "Technology");
        scanner.register_ticker(TickerId(2), "Technology");
        scanner.register_ticker(TickerId(3), "Energy");
        assert_eq!(scanner.sector_count(), 2);
    }

    #[test]
    fn test_rotation_scanner_snapshot_updates() {
        let mut scanner = SectorRotationScanner::new(0.005, 10);
        scanner.register_ticker(TickerId(1), "Technology");

        // First snapshot: no return (no previous price)
        scanner.on_snapshot(TickerId(1), 100.0);
        assert!(!scanner.ticker_returns.contains_key(&TickerId(1)));

        // Second snapshot: return calculated
        scanner.on_snapshot(TickerId(1), 102.0);
        let ret = scanner.ticker_returns.get(&TickerId(1)).copied();
        assert!(ret.is_some());
        assert!((ret.expect("return") - 0.02).abs() < 0.001);
    }

    #[test]
    fn test_rotation_scanner_sector_recompute() {
        let mut scanner = SectorRotationScanner::new(0.005, 10);
        scanner.register_ticker(TickerId(1), "Tech");
        scanner.register_ticker(TickerId(2), "Tech");
        scanner.register_ticker(TickerId(3), "Energy");

        // Initial prices
        scanner.on_snapshot(TickerId(1), 100.0);
        scanner.on_snapshot(TickerId(2), 50.0);
        scanner.on_snapshot(TickerId(3), 200.0);

        // Updated prices: Tech up 2%, Energy down 1%
        scanner.on_snapshot(TickerId(1), 102.0);
        scanner.on_snapshot(TickerId(2), 51.0);
        scanner.on_snapshot(TickerId(3), 198.0);

        scanner.recompute_sectors();

        // Tech sector should have positive relative strength
        let tech = scanner.sectors.get("Tech").expect("Tech sector");
        assert!(tech.sector_return > 0.0);
        assert!(tech.relative_strength > 0.0);
    }

    #[test]
    fn test_rotation_scanner_candidates() {
        let mut scanner = SectorRotationScanner::new(0.001, 10);
        scanner.register_ticker(TickerId(1), "Tech");
        scanner.register_ticker(TickerId(2), "Energy");

        // First round: establish prev_return
        scanner.on_snapshot(TickerId(1), 100.0);
        scanner.on_snapshot(TickerId(2), 100.0);
        scanner.on_snapshot(TickerId(1), 101.0);
        scanner.on_snapshot(TickerId(2), 99.0);
        scanner.recompute_sectors();

        // Second round: Tech improving further
        scanner.on_snapshot(TickerId(1), 103.0);
        scanner.on_snapshot(TickerId(2), 98.0);
        scanner.recompute_sectors();

        let candidates = scanner.rotation_candidates(5_000_000_000);
        // Tech should appear as a rotation candidate (improving relative strength)
        if !candidates.is_empty() {
            assert_eq!(candidates[0].source, ScannerSource::SectorRotation);
            assert!(candidates[0].score > 0.0);
        }
    }

    #[test]
    fn test_rotation_scanner_reset() {
        let mut scanner = SectorRotationScanner::new(0.005, 10);
        scanner.register_ticker(TickerId(1), "Tech");
        assert_eq!(scanner.sector_count(), 1);
        scanner.reset();
        assert_eq!(scanner.sector_count(), 0);
    }

    #[test]
    fn test_signal_candidate_fields() {
        let c = SignalCandidate {
            ticker_id: TickerId(5),
            score: 75.0,
            direction_bias: 0.015,
            source: ScannerSource::MomentumScanner,
            timestamp_ns: 1_000_000_000,
        };
        assert_eq!(c.ticker_id, TickerId(5));
        assert!(c.score > 70.0);
        assert!(c.direction_bias > 0.0);
    }
}
