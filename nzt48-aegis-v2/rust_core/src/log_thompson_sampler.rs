//! P5-C: Log-Transform Thompson Sampling (Russo et al. 2018).
//! Multi-armed bandit for ticker allocation in SubscriptionManager rotation.
//! Uses log-normal posterior to handle positive skew in momentum winners.
//! Gaussian Bandit penalizes positive skew — log-transform fixes this.

use std::collections::HashMap;

use crate::types::TickerId;

/// Per-arm (ticker) state for log-Thompson sampling.
#[derive(Clone, Debug)]
pub struct ArmState {
    /// Number of times this arm has been pulled.
    pub pulls: u64,
    /// Sum of log-returns observed.
    pub sum_log_returns: f64,
    /// Sum of squared log-returns (for variance estimation).
    pub sum_sq_log_returns: f64,
    /// Posterior mean of log-return.
    pub mu: f64,
    /// Posterior variance of log-return.
    pub tau2: f64,
}

impl ArmState {
    fn new() -> Self {
        Self {
            pulls: 0,
            sum_log_returns: 0.0,
            sum_sq_log_returns: 0.0,
            mu: 0.0,
            tau2: 1.0, // High initial uncertainty (exploration)
        }
    }

    /// Update posterior with a new observed return.
    fn observe(&mut self, return_pct: f64) {
        // Log-transform: work with ln(1 + r) to handle positive skew.
        let log_r = (1.0 + return_pct / 100.0).max(0.001).ln();
        self.pulls += 1;
        self.sum_log_returns += log_r;
        self.sum_sq_log_returns += log_r * log_r;

        // Update posterior (conjugate normal-normal with known variance).
        // Prior: N(0, 1). Likelihood: N(mu, sigma²).
        let n = self.pulls as f64;
        let sample_mean = self.sum_log_returns / n;
        let sample_var = if n > 1.0 {
            (self.sum_sq_log_returns / n - sample_mean * sample_mean).max(0.001)
        } else {
            1.0
        };

        // Posterior precision = prior_precision + n / sigma²
        let prior_precision = 1.0; // Prior: N(0, 1)
        let likelihood_precision = n / sample_var;
        let posterior_precision = prior_precision + likelihood_precision;

        self.tau2 = 1.0 / posterior_precision;
        self.mu = self.tau2 * (likelihood_precision * sample_mean);
    }
}

/// Log-Thompson Sampler for multi-ticker allocation.
pub struct LogThompsonSampler {
    arms: HashMap<TickerId, ArmState>,
    /// Deterministic seed for reproducible sampling (PRNG state).
    seed: u64,
}

impl LogThompsonSampler {
    pub fn new() -> Self {
        Self {
            arms: HashMap::new(),
            seed: 42,
        }
    }

    /// Register a new arm (ticker) if not already present.
    pub fn register(&mut self, ticker_id: TickerId) {
        self.arms.entry(ticker_id).or_insert_with(ArmState::new);
    }

    /// Observe a return for a ticker.
    pub fn observe(&mut self, ticker_id: TickerId, return_pct: f64) {
        self.arms
            .entry(ticker_id)
            .or_insert_with(ArmState::new)
            .observe(return_pct);
    }

    /// Sample from posterior for each arm, return top-k tickers.
    /// Uses Box-Muller transform with deterministic PRNG for reproducibility.
    pub fn select_top_k(&mut self, k: usize) -> Vec<TickerId> {
        // Pre-generate random values to avoid borrow conflict with self.arms.iter()
        let n = self.arms.len();
        let normals: Vec<f64> = (0..n).map(|_| self.next_normal()).collect();

        let mut samples: Vec<(TickerId, f64)> = self
            .arms
            .iter()
            .zip(normals.iter())
            .map(|((&tid, arm), &z)| {
                let sample = arm.mu + arm.tau2.sqrt() * z;
                // Transform back: E[exp(X)] for log-normal
                let expected_return = sample.exp();
                (tid, expected_return)
            })
            .collect();

        // Sort descending by sampled expected return.
        samples.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        samples.truncate(k);
        samples.into_iter().map(|(tid, _)| tid).collect()
    }

    /// Get the arm state for a ticker.
    pub fn arm(&self, ticker_id: TickerId) -> Option<&ArmState> {
        self.arms.get(&ticker_id)
    }

    pub fn arm_count(&self) -> usize {
        self.arms.len()
    }

    /// Simple deterministic PRNG (xorshift64).
    fn next_u64(&mut self) -> u64 {
        self.seed ^= self.seed << 13;
        self.seed ^= self.seed >> 7;
        self.seed ^= self.seed << 17;
        self.seed
    }

    /// Generate a standard normal sample via Box-Muller (deterministic).
    fn next_normal(&mut self) -> f64 {
        let u1 = (self.next_u64() as f64) / (u64::MAX as f64);
        let u2 = (self.next_u64() as f64) / (u64::MAX as f64);
        let u1 = u1.max(1e-15); // Avoid log(0)
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }
}

impl Default for LogThompsonSampler {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_arm_state_initial() {
        let arm = ArmState::new();
        assert_eq!(arm.pulls, 0);
        assert_eq!(arm.mu, 0.0);
        assert_eq!(arm.tau2, 1.0);
    }

    #[test]
    fn test_arm_observe_updates_posterior() {
        let mut arm = ArmState::new();
        arm.observe(2.0); // +2% return
        assert_eq!(arm.pulls, 1);
        assert!(arm.mu > 0.0, "Positive return should shift mu positive");
        assert!(arm.tau2 < 1.0, "Observation should reduce uncertainty");
    }

    #[test]
    fn test_sampler_register_and_observe() {
        let mut sampler = LogThompsonSampler::new();
        let tid = TickerId(0);
        sampler.register(tid);
        sampler.observe(tid, 1.5);
        sampler.observe(tid, -0.5);
        assert_eq!(sampler.arm_count(), 1);
        let arm = sampler.arm(tid).expect("arm exists");
        assert_eq!(arm.pulls, 2);
    }

    #[test]
    fn test_sampler_select_top_k() {
        let mut sampler = LogThompsonSampler::new();
        // Create 5 arms with different return profiles
        for i in 0..5 {
            let tid = TickerId(i);
            sampler.register(tid);
            for _ in 0..20 {
                let ret = (i as f64 - 2.0) * 0.5; // Arms 0-4 get returns -1, -0.5, 0, 0.5, 1
                sampler.observe(tid, ret);
            }
        }
        let top3 = sampler.select_top_k(3);
        assert_eq!(top3.len(), 3);
        // Arms with higher returns should be selected more often
        // (not guaranteed on every single draw due to exploration, but likely)
    }

    #[test]
    fn test_sampler_handles_extreme_returns() {
        let mut sampler = LogThompsonSampler::new();
        let tid = TickerId(0);
        sampler.observe(tid, 50.0);  // Extreme positive
        sampler.observe(tid, -50.0); // Extreme negative
        // Should not panic
        let arm = sampler.arm(tid).expect("arm exists");
        assert_eq!(arm.pulls, 2);
    }

    #[test]
    fn test_sampler_select_returns_correct_count() {
        let mut sampler = LogThompsonSampler::new();
        for i in 0..10 {
            let tid = TickerId(i);
            sampler.register(tid);
            sampler.observe(tid, 1.0);
        }
        let top5 = sampler.select_top_k(5);
        assert_eq!(top5.len(), 5, "Should return exactly k items");
        let top20 = sampler.select_top_k(20);
        assert_eq!(top20.len(), 10, "Should return at most arm_count items");
    }
}
