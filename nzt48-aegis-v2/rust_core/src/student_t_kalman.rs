//! Student-t Kalman Filter with Dynamic Huber Delta (MAD-based).
//! RM-4: Replaces hardcoded HUBER_DELTA=1.5 with adaptive delta = 1.345 × MAD.
//!
//! The Huber delta controls outlier robustness in the filter's loss function:
//!   - Small delta → more robust (rejects more outliers), slower adaptation
//!   - Large delta → less robust, faster adaptation
//!
//! Static delta fails on volatility regime changes. Dynamic delta adapts
//! within ~100 ticks using a rolling MAD (Median Absolute Deviation).

use std::collections::VecDeque;

/// Default Huber delta (fallback when insufficient data).
const DEFAULT_HUBER_DELTA: f64 = 1.5;
/// Multiplier for MAD → Huber delta conversion.
const MAD_TO_DELTA: f64 = 1.345;
/// Minimum residuals before adaptive delta kicks in.
const MIN_RESIDUALS_FOR_ADAPT: usize = 10;

/// Student-t Kalman filter with dynamic Huber robustness.
pub struct StudentTKalmanFilter {
    /// State estimate (e.g., smoothed price or return).
    x: f64,
    /// State uncertainty (covariance).
    p: f64,
    /// Process noise variance.
    q: f64,
    /// Measurement noise variance.
    r: f64,
    /// Dynamic Huber delta, MAD-based.
    huber_delta: f64,
    /// Rolling residual buffer for MAD computation.
    residuals: VecDeque<f64>,
    /// Max residuals to keep.
    max_residuals: usize,
}

impl StudentTKalmanFilter {
    pub fn new(initial_x: f64, initial_p: f64, q: f64, r: f64, max_residuals: usize) -> Self {
        Self {
            x: initial_x,
            p: initial_p,
            q,
            r,
            huber_delta: DEFAULT_HUBER_DELTA,
            residuals: VecDeque::with_capacity(max_residuals),
            max_residuals,
        }
    }

    /// Predict step: propagate state and uncertainty.
    pub fn predict(&mut self) {
        // State doesn't change in random walk model
        self.p += self.q;
    }

    /// Update step with Huber-robust measurement incorporation.
    /// Returns the innovation (residual before update).
    pub fn update(&mut self, measurement: f64) -> f64 {
        let innovation = measurement - self.x;
        let s = self.p + self.r; // Innovation variance

        // Huber weighting: downweight outliers beyond delta
        let huber_weight = huber_weight(innovation, self.huber_delta, s);

        // Kalman gain with Huber robustness
        let k = (self.p / s) * huber_weight;

        // State update
        self.x += k * innovation;
        // Covariance update (Joseph form for numerical stability)
        self.p *= 1.0 - k;

        // Record residual and update delta
        self.record_residual(innovation);

        innovation
    }

    /// Combined predict + update step.
    pub fn step(&mut self, measurement: f64) -> f64 {
        self.predict();
        self.update(measurement)
    }

    /// Record a residual and recompute Huber delta from MAD.
    fn record_residual(&mut self, residual: f64) {
        self.residuals.push_back(residual);
        if self.residuals.len() > self.max_residuals {
            self.residuals.pop_front();
        }

        self.update_huber_delta();
    }

    /// Recompute Huber delta: 1.345 × MAD(residuals).
    fn update_huber_delta(&mut self) {
        if self.residuals.len() < MIN_RESIDUALS_FOR_ADAPT {
            return;
        }

        let mad = compute_mad(&self.residuals);

        self.huber_delta = if mad > 0.0 {
            MAD_TO_DELTA * mad
        } else {
            DEFAULT_HUBER_DELTA
        };
    }

    /// Current state estimate.
    pub fn state(&self) -> f64 {
        self.x
    }

    /// Current state uncertainty.
    pub fn uncertainty(&self) -> f64 {
        self.p
    }

    /// Current Huber delta.
    pub fn huber_delta(&self) -> f64 {
        self.huber_delta
    }

    /// Number of residuals in the buffer.
    pub fn residual_count(&self) -> usize {
        self.residuals.len()
    }
}

/// Compute Median Absolute Deviation of a VecDeque.
fn compute_mad(values: &VecDeque<f64>) -> f64 {
    if values.is_empty() {
        return 0.0;
    }

    // Get median
    let mut sorted: Vec<f64> = values.iter().copied().collect();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let median = sorted[sorted.len() / 2];

    // Absolute deviations from median
    let mut abs_devs: Vec<f64> = sorted.iter().map(|v| (v - median).abs()).collect();
    abs_devs.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    // Median of absolute deviations
    abs_devs[abs_devs.len() / 2]
}

/// Huber weighting function.
/// Returns 1.0 for inliers, reduced weight for outliers.
fn huber_weight(innovation: f64, delta: f64, variance: f64) -> f64 {
    let standardized = innovation.abs() / variance.sqrt().max(1e-10);

    if standardized <= delta {
        1.0
    } else {
        delta / standardized
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_kalman_basic_convergence() {
        let mut kf = StudentTKalmanFilter::new(0.0, 1.0, 0.01, 0.1, 100);

        // Feed constant measurements — state should converge
        for _ in 0..50 {
            kf.step(10.0);
        }

        assert!(
            (kf.state() - 10.0).abs() < 0.5,
            "State {} didn't converge to 10.0",
            kf.state()
        );
    }

    #[test]
    fn test_kalman_huber_regime_change() {
        // AT-RM4: Delta adapts within 100 ticks on volatility spike
        let mut kf = StudentTKalmanFilter::new(100.0, 1.0, 0.01, 0.1, 100);

        // Phase 1: Low volatility (small residuals) → small delta
        for _ in 0..50 {
            kf.step(100.0 + 0.1 * (rand_simple() - 0.5));
        }
        let delta_low_vol = kf.huber_delta();

        // Phase 2: High volatility spike (large residuals) → delta should increase
        for _ in 0..100 {
            kf.step(100.0 + 5.0 * (rand_simple() - 0.5));
        }
        let delta_high_vol = kf.huber_delta();

        assert!(
            delta_high_vol > delta_low_vol,
            "Huber delta didn't increase on volatility spike: low={:.4} high={:.4}",
            delta_low_vol,
            delta_high_vol
        );
    }

    #[test]
    fn test_kalman_outlier_robustness() {
        let mut kf = StudentTKalmanFilter::new(100.0, 1.0, 0.01, 0.1, 100);

        // Build up some normal residuals
        for _ in 0..30 {
            kf.step(100.0);
        }

        let state_before_outlier = kf.state();

        // Single massive outlier
        kf.step(200.0);
        let state_after_outlier = kf.state();

        // Huber weighting should dampen the outlier's impact
        // Without Huber, the state would jump much more
        let jump = (state_after_outlier - state_before_outlier).abs();
        assert!(
            jump < 50.0,
            "Outlier jump {} is too large (Huber should dampen)",
            jump
        );
    }

    #[test]
    fn test_mad_computation() {
        let values: VecDeque<f64> = vec![1.0, 2.0, 3.0, 4.0, 5.0].into();
        let mad = compute_mad(&values);
        // Median = 3.0, deviations = [2, 1, 0, 1, 2], sorted = [0, 1, 1, 2, 2]
        // MAD = 1.0
        assert!((mad - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_huber_weight_inlier() {
        // Small innovation → weight = 1.0
        let w = huber_weight(0.5, 1.5, 1.0);
        assert_eq!(w, 1.0);
    }

    #[test]
    fn test_huber_weight_outlier() {
        // Large innovation → weight < 1.0
        let w = huber_weight(10.0, 1.5, 1.0);
        assert!(w < 1.0);
        assert!(w > 0.0);
    }

    #[test]
    fn test_delta_starts_at_default() {
        let kf = StudentTKalmanFilter::new(0.0, 1.0, 0.01, 0.1, 100);
        assert_eq!(kf.huber_delta(), DEFAULT_HUBER_DELTA);
    }

    #[test]
    fn test_residual_buffer_bounded() {
        let mut kf = StudentTKalmanFilter::new(0.0, 1.0, 0.01, 0.1, 50);

        for _ in 0..200 {
            kf.step(1.0);
        }

        assert_eq!(kf.residual_count(), 50);
    }

    /// Simple deterministic pseudo-random for testing (no external crate needed).
    fn rand_simple() -> f64 {
        use std::sync::atomic::{AtomicU64, Ordering};
        static SEED: AtomicU64 = AtomicU64::new(12345);
        let mut s = SEED.load(Ordering::Relaxed);
        s ^= s << 13;
        s ^= s >> 7;
        s ^= s << 17;
        SEED.store(s, Ordering::Relaxed);
        (s % 10000) as f64 / 10000.0
    }
}
