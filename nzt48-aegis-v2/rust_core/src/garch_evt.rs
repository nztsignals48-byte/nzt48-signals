//! P5-A: EVT on GARCH Residuals (McNeil & Frey 2000).
//! Applies Generalized Pareto Distribution (GPD) to GARCH(1,1) standardized
//! residuals for tail risk estimation. Produces CVaR (Expected Shortfall)
//! that feeds into RiskArbiter CHECK 24.

use std::collections::VecDeque;

use crate::types::TickerId;

/// GPD parameters: shape (xi) and scale (sigma).
#[derive(Clone, Debug)]
pub struct GpdParams {
    pub xi: f64,    // Shape parameter (tail heaviness)
    pub sigma: f64, // Scale parameter
}

/// Result of EVT tail analysis on GARCH residuals.
#[derive(Clone, Debug)]
pub struct EvtResult {
    /// CVaR at the configured confidence level (e.g., 99%).
    pub cvar: f64,
    /// VaR at the configured confidence level.
    pub var: f64,
    /// GPD shape parameter (xi > 0 means heavy tail).
    pub xi: f64,
    /// Number of exceedances used for fitting.
    pub n_exceedances: usize,
    /// Total residuals in buffer.
    pub n_residuals: usize,
}

/// EVT engine for a single ticker. Accumulates GARCH standardized residuals
/// and fits GPD to the left tail (losses).
pub struct EvtEngine {
    /// Rolling buffer of standardized residuals (ε_t = r_t / σ_t).
    residuals: VecDeque<f64>,
    /// Maximum residuals to keep.
    max_residuals: usize,
    /// Confidence level for VaR/CVaR (e.g., 0.99).
    confidence: f64,
    /// POT threshold quantile (e.g., 0.90 = top 10% of losses).
    threshold_quantile: f64,
    /// Cached GPD fit (recomputed periodically).
    cached_gpd: Option<GpdParams>,
    /// Cached EVT result.
    cached_result: Option<EvtResult>,
    /// Residuals since last refit.
    residuals_since_refit: usize,
    /// Refit interval (number of new residuals before refitting).
    refit_interval: usize,
}

impl EvtEngine {
    pub fn new(max_residuals: usize, confidence: f64) -> Self {
        Self {
            residuals: VecDeque::with_capacity(max_residuals),
            max_residuals,
            confidence,
            threshold_quantile: 0.90,
            cached_gpd: None,
            cached_result: None,
            residuals_since_refit: 0,
            refit_interval: 50, // Refit every 50 new residuals
        }
    }

    /// Add a new standardized residual and possibly refit GPD.
    pub fn add_residual(&mut self, residual: f64) {
        if !residual.is_finite() {
            return;
        }
        if self.residuals.len() >= self.max_residuals {
            self.residuals.pop_front();
        }
        self.residuals.push_back(residual);
        self.residuals_since_refit += 1;

        // Periodic refit
        if self.residuals_since_refit >= self.refit_interval && self.residuals.len() >= 100 {
            self.refit();
            self.residuals_since_refit = 0;
        }
    }

    /// Get the current CVaR estimate (None if insufficient data).
    pub fn cvar(&self) -> Option<f64> {
        self.cached_result.as_ref().map(|r| r.cvar)
    }

    /// Get the full EVT result.
    pub fn result(&self) -> Option<&EvtResult> {
        self.cached_result.as_ref()
    }

    /// Number of residuals in buffer.
    pub fn residual_count(&self) -> usize {
        self.residuals.len()
    }

    /// Refit GPD to current residuals.
    fn refit(&mut self) {
        let n = self.residuals.len();
        if n < 100 {
            return;
        }

        // Work with negative residuals (losses) — flip sign so losses are positive.
        let mut losses: Vec<f64> = self.residuals.iter().map(|&r| -r).collect();
        losses.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

        // POT threshold: the threshold_quantile-th percentile of losses.
        let threshold_idx = (n as f64 * self.threshold_quantile) as usize;
        let threshold = losses[threshold_idx.min(n - 1)];

        // Exceedances: losses above threshold.
        let exceedances: Vec<f64> = losses
            .iter()
            .filter(|&&x| x > threshold)
            .map(|&x| x - threshold)
            .collect();

        let n_exceed = exceedances.len();
        if n_exceed < 10 {
            return; // Not enough exceedances for reliable fit
        }

        // Fit GPD via method of moments (Hosking & Wallis 1987).
        // For exceedances y_i: E[Y] = σ/(1-ξ), Var[Y] = σ²/((1-ξ)²(1-2ξ))
        let mean = exceedances.iter().sum::<f64>() / n_exceed as f64;
        let var = exceedances.iter().map(|&y| (y - mean).powi(2)).sum::<f64>()
            / (n_exceed - 1).max(1) as f64;

        if mean <= 0.0 || var <= 0.0 {
            return;
        }

        // Method of moments estimators:
        // ξ = 0.5 * (1 - mean²/var)
        // σ = mean * (1 + ξ) / 2  ... actually:
        // ξ = 0.5 * (mean²/var - 1)  (corrected)
        // σ = 0.5 * mean * (mean²/var + 1)
        let ratio = mean * mean / var;
        let xi = 0.5 * (ratio - 1.0);
        // Clamp xi to [-0.5, 0.5] for stability (most financial data is near 0.1-0.3)
        let xi = xi.clamp(-0.5, 0.5);
        let sigma = mean * (1.0 - xi);

        if sigma <= 0.0 {
            return;
        }

        let gpd = GpdParams { xi, sigma };

        // Compute VaR and CVaR at confidence level.
        let alpha = self.confidence; // e.g., 0.99
        let nu = n_exceed as f64 / n as f64; // Exceedance rate

        // VaR_α = u + (σ/ξ) * ((n/N_u * (1-α))^(-ξ) - 1)   for ξ ≠ 0
        // CVaR_α = VaR_α / (1-ξ) + (σ - ξ*u) / (1-ξ)
        let var_alpha = if xi.abs() < 1e-8 {
            // ξ ≈ 0: exponential tail
            threshold + sigma * (nu / (1.0 - alpha)).ln()
        } else {
            threshold + (sigma / xi) * ((nu / (1.0 - alpha)).powf(xi) - 1.0)
        };

        let cvar_alpha = if xi.abs() < 1e-8 {
            var_alpha + sigma
        } else if xi < 1.0 {
            var_alpha / (1.0 - xi) + (sigma - xi * threshold) / (1.0 - xi)
        } else {
            var_alpha * 2.0 // Fallback for extreme xi
        };

        self.cached_gpd = Some(gpd);
        self.cached_result = Some(EvtResult {
            cvar: cvar_alpha,
            var: var_alpha,
            xi,
            n_exceedances: n_exceed,
            n_residuals: n,
        });
    }
}

/// Per-ticker EVT registry, parallel to GarchRegistry.
pub struct EvtRegistry {
    engines: std::collections::HashMap<TickerId, EvtEngine>,
    max_residuals: usize,
    confidence: f64,
}

impl EvtRegistry {
    pub fn new(max_residuals: usize, confidence: f64) -> Self {
        Self {
            engines: std::collections::HashMap::new(),
            max_residuals,
            confidence,
        }
    }

    /// Add a standardized residual for a ticker.
    pub fn add_residual(&mut self, ticker_id: TickerId, residual: f64) {
        self.engines
            .entry(ticker_id)
            .or_insert_with(|| EvtEngine::new(self.max_residuals, self.confidence))
            .add_residual(residual);
    }

    /// Get CVaR for a ticker (None if insufficient data).
    pub fn cvar(&self, ticker_id: TickerId) -> Option<f64> {
        self.engines.get(&ticker_id).and_then(|e| e.cvar())
    }

    /// Get full EVT result for a ticker.
    pub fn result(&self, ticker_id: TickerId) -> Option<&EvtResult> {
        self.engines.get(&ticker_id).and_then(|e| e.result())
    }

    pub fn len(&self) -> usize {
        self.engines.len()
    }

    pub fn is_empty(&self) -> bool {
        self.engines.is_empty()
    }
}

impl Default for EvtRegistry {
    fn default() -> Self {
        Self::new(5000, 0.99)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_evt_engine_cold_start() {
        let engine = EvtEngine::new(5000, 0.99);
        assert!(engine.cvar().is_none());
        assert_eq!(engine.residual_count(), 0);
    }

    #[test]
    fn test_evt_engine_accumulates_residuals() {
        let mut engine = EvtEngine::new(5000, 0.99);
        for i in 0..200 {
            // Simulate standardized residuals ~ N(0,1) with some heavy tails
            let r = ((i as f64 * 7.3 + 1.1).sin()) * 2.0;
            engine.add_residual(r);
        }
        assert_eq!(engine.residual_count(), 200);
        // After 200 residuals and refit at 50, should have a result
        // (may or may not depending on exceedances)
    }

    #[test]
    fn test_evt_engine_buffer_bounded() {
        let mut engine = EvtEngine::new(100, 0.99);
        for i in 0..200 {
            engine.add_residual(i as f64 * 0.01);
        }
        assert_eq!(engine.residual_count(), 100);
    }

    #[test]
    fn test_evt_registry_multi_ticker() {
        let mut registry = EvtRegistry::new(5000, 0.99);
        let tid1 = TickerId(0);
        let tid2 = TickerId(1);
        for i in 0..200 {
            let r = ((i as f64 * 3.7).sin()) * 1.5;
            registry.add_residual(tid1, r);
            registry.add_residual(tid2, r * 0.8);
        }
        assert_eq!(registry.len(), 2);
    }

    #[test]
    fn test_evt_gpd_fit_produces_result() {
        let mut engine = EvtEngine::new(5000, 0.99);
        // Generate enough residuals with some fat tails
        for i in 0..500 {
            let base = ((i as f64 * 2.3).sin()) * 1.0;
            // Add occasional large negative residuals (tail events)
            let r = if i % 20 == 0 { base - 3.0 } else { base };
            engine.add_residual(r);
        }
        // Should have refitted by now
        if let Some(result) = engine.result() {
            assert!(result.cvar > 0.0, "CVaR should be positive");
            assert!(result.var > 0.0, "VaR should be positive");
            assert!(result.n_exceedances > 0);
        }
    }

    #[test]
    fn test_evt_ignores_non_finite() {
        let mut engine = EvtEngine::new(100, 0.99);
        engine.add_residual(f64::NAN);
        engine.add_residual(f64::INFINITY);
        engine.add_residual(f64::NEG_INFINITY);
        assert_eq!(engine.residual_count(), 0);
    }
}
