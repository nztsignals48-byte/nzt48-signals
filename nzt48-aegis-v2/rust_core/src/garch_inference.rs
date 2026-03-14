//! GARCH(1,1) Real-Time Inference — O(1) residual calculation per tick.
//! Nightly fit via Python `arch` library produces (omega, alpha, beta, sigma2_prev).
//! This module consumes those params and performs O(1) conditional variance updates.
//! RM-1: Prevents Tokio reactor freeze from per-tick MLE optimization.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::Path;

use crate::types::TickerId;

/// GARCH(1,1) parameters fitted nightly by Python Ouroboros.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct GarchParams {
    /// Long-run variance (intercept)
    pub omega: f64,
    /// ARCH coefficient (shock sensitivity)
    pub alpha: f64,
    /// GARCH coefficient (persistence)
    pub beta: f64,
    /// Last conditional variance from nightly fit
    pub sigma2_prev: f64,
}

impl GarchParams {
    /// Validate parameter constraints: omega > 0, alpha >= 0, beta >= 0, alpha + beta < 1.
    pub fn is_valid(&self) -> bool {
        self.omega > 0.0
            && self.alpha >= 0.0
            && self.beta >= 0.0
            && (self.alpha + self.beta) < 1.0
            && self.sigma2_prev > 0.0
            && self.omega.is_finite()
            && self.alpha.is_finite()
            && self.beta.is_finite()
            && self.sigma2_prev.is_finite()
    }
}

/// Per-ticker GARCH inference engine. O(1) update per tick.
#[derive(Clone, Debug)]
pub struct GarchInference {
    omega: f64,
    alpha: f64,
    beta: f64,
    sigma2_prev: f64,
}

impl GarchInference {
    pub fn new(params: &GarchParams) -> Self {
        Self {
            omega: params.omega,
            alpha: params.alpha,
            beta: params.beta,
            sigma2_prev: params.sigma2_prev,
        }
    }

    /// O(1) GARCH(1,1) recursion: σ²_t = ω + α·r²_{t-1} + β·σ²_{t-1}
    /// Returns the standardized residual: r_t / σ_t
    pub fn update_residual(&mut self, return_: f64) -> f64 {
        let sigma2 = self.omega + self.alpha * return_.powi(2) + self.beta * self.sigma2_prev;

        // Guard against degenerate values
        let sigma2 = if sigma2 > 0.0 && sigma2.is_finite() {
            sigma2
        } else {
            self.sigma2_prev // fallback to previous
        };

        self.sigma2_prev = sigma2;
        let sigma = sigma2.sqrt();

        if sigma > 0.0 {
            return_ / sigma
        } else {
            0.0
        }
    }

    /// Current conditional variance (σ²_t).
    pub fn sigma2(&self) -> f64 {
        self.sigma2_prev
    }

    /// Current conditional volatility (σ_t).
    pub fn sigma(&self) -> f64 {
        self.sigma2_prev.sqrt()
    }
}

/// Registry of GARCH inference engines, one per ticker.
pub struct GarchRegistry {
    engines: HashMap<TickerId, GarchInference>,
}

impl GarchRegistry {
    /// Load GARCH params from JSON file produced by Python nightly calibration.
    /// File format: { "ticker_id_num": { "omega": ..., "alpha": ..., "beta": ..., "sigma2_prev": ... }, ... }
    pub fn load(path: &Path) -> Result<Self, GarchError> {
        let contents = fs::read_to_string(path).map_err(GarchError::Io)?;
        let raw: HashMap<String, GarchParams> =
            serde_json::from_str(&contents).map_err(GarchError::Parse)?;

        let mut engines = HashMap::new();
        let mut skipped = 0;

        for (key, params) in &raw {
            let ticker_id: u32 = match key.parse() {
                Ok(id) => id,
                Err(_) => {
                    skipped += 1;
                    continue;
                }
            };

            if !params.is_valid() {
                skipped += 1;
                continue;
            }

            engines.insert(TickerId(ticker_id), GarchInference::new(params));
        }

        if skipped > 0 {
            eprintln!(
                "GARCH: loaded {} engines, skipped {} invalid",
                engines.len(),
                skipped
            );
        }

        Ok(Self { engines })
    }

    /// Create empty registry (cold start — no GARCH params available yet).
    pub fn empty() -> Self {
        Self {
            engines: HashMap::new(),
        }
    }

    /// Update residual for a ticker. Returns None if no GARCH engine for this ticker.
    pub fn update_residual(&mut self, ticker_id: TickerId, return_: f64) -> Option<f64> {
        self.engines
            .get_mut(&ticker_id)
            .map(|engine| engine.update_residual(return_))
    }

    /// Get current conditional volatility for a ticker.
    pub fn sigma(&self, ticker_id: TickerId) -> Option<f64> {
        self.engines.get(&ticker_id).map(|e| e.sigma())
    }

    /// Number of registered engines.
    pub fn len(&self) -> usize {
        self.engines.len()
    }

    pub fn is_empty(&self) -> bool {
        self.engines.is_empty()
    }
}

#[derive(Debug)]
pub enum GarchError {
    Io(std::io::Error),
    Parse(serde_json::Error),
}

impl std::fmt::Display for GarchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            GarchError::Io(e) => write!(f, "GARCH IO error: {e}"),
            GarchError::Parse(e) => write!(f, "GARCH parse error: {e}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_garch_inference_o1_residual() {
        let params = GarchParams {
            omega: 0.00001,
            alpha: 0.10,
            beta: 0.85,
            sigma2_prev: 0.0004, // ~2% daily vol
        };
        assert!(params.is_valid());

        let mut engine = GarchInference::new(&params);

        // Simulate a return of 1%
        let residual = engine.update_residual(0.01);

        // sigma2 = 0.00001 + 0.10 * 0.01^2 + 0.85 * 0.0004
        //        = 0.00001 + 0.00001 + 0.00034 = 0.00036
        // sigma = sqrt(0.00036) ≈ 0.01897
        // residual = 0.01 / 0.01897 ≈ 0.527
        assert!((engine.sigma2() - 0.00036).abs() < 1e-10);
        assert!((residual - 0.527).abs() < 0.01);
    }

    #[test]
    fn test_garch_inference_multiple_updates() {
        let params = GarchParams {
            omega: 0.00001,
            alpha: 0.10,
            beta: 0.85,
            sigma2_prev: 0.0004,
        };
        let mut engine = GarchInference::new(&params);

        // 100 ticks — O(1) per tick, no accumulation issues
        let returns = [0.01, -0.02, 0.005, -0.015, 0.008];
        for &r in returns.iter().cycle().take(100) {
            let residual = engine.update_residual(r);
            assert!(residual.is_finite());
            assert!(engine.sigma2() > 0.0);
        }
    }

    #[test]
    fn test_garch_params_validation() {
        // Valid
        assert!(GarchParams {
            omega: 0.00001,
            alpha: 0.10,
            beta: 0.85,
            sigma2_prev: 0.0004,
        }
        .is_valid());

        // alpha + beta >= 1 (non-stationary)
        assert!(!GarchParams {
            omega: 0.00001,
            alpha: 0.50,
            beta: 0.55,
            sigma2_prev: 0.0004,
        }
        .is_valid());

        // omega <= 0
        assert!(!GarchParams {
            omega: 0.0,
            alpha: 0.10,
            beta: 0.85,
            sigma2_prev: 0.0004,
        }
        .is_valid());

        // NaN
        assert!(!GarchParams {
            omega: f64::NAN,
            alpha: 0.10,
            beta: 0.85,
            sigma2_prev: 0.0004,
        }
        .is_valid());
    }

    #[test]
    fn test_garch_inference_zero_return() {
        let params = GarchParams {
            omega: 0.00001,
            alpha: 0.10,
            beta: 0.85,
            sigma2_prev: 0.0004,
        };
        let mut engine = GarchInference::new(&params);
        let residual = engine.update_residual(0.0);
        assert_eq!(residual, 0.0);
        // sigma2 = omega + beta * sigma2_prev (alpha term is 0)
        let expected = 0.00001 + 0.85 * 0.0004;
        assert!((engine.sigma2() - expected).abs() < 1e-10);
    }

    #[test]
    fn test_garch_registry_load() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("garch_params.json");

        let json = serde_json::json!({
            "1": { "omega": 0.00001, "alpha": 0.10, "beta": 0.85, "sigma2_prev": 0.0004 },
            "2": { "omega": 0.00002, "alpha": 0.08, "beta": 0.88, "sigma2_prev": 0.0005 },
            "999": { "omega": 0.0, "alpha": 0.10, "beta": 0.85, "sigma2_prev": 0.0004 }
        });

        let mut f = fs::File::create(&path).expect("create");
        write!(f, "{}", json).expect("write");

        let registry = GarchRegistry::load(&path).expect("load");
        // 999 should be skipped (omega = 0)
        assert_eq!(registry.len(), 2);
        assert!(registry.sigma(TickerId(1)).is_some());
        assert!(registry.sigma(TickerId(2)).is_some());
        assert!(registry.sigma(TickerId(999)).is_none());
    }

    #[test]
    fn test_garch_registry_update_residual() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("garch_params.json");

        let json = serde_json::json!({
            "42": { "omega": 0.00001, "alpha": 0.10, "beta": 0.85, "sigma2_prev": 0.0004 }
        });

        let mut f = fs::File::create(&path).expect("create");
        write!(f, "{}", json).expect("write");

        let mut registry = GarchRegistry::load(&path).expect("load");

        // Known ticker
        let r = registry.update_residual(TickerId(42), 0.01);
        assert!(r.is_some());
        assert!(r.expect("residual").is_finite());

        // Unknown ticker
        assert!(registry.update_residual(TickerId(999), 0.01).is_none());
    }

    #[test]
    fn test_garch_registry_empty() {
        let registry = GarchRegistry::empty();
        assert!(registry.is_empty());
        assert_eq!(registry.len(), 0);
    }

    #[test]
    fn test_garch_inference_degenerate_guard() {
        // If somehow sigma2 goes negative or NaN, it should fallback
        let params = GarchParams {
            omega: 1e-20,
            alpha: 0.01,
            beta: 0.01,
            sigma2_prev: 1e-20,
        };
        let mut engine = GarchInference::new(&params);
        let residual = engine.update_residual(0.0);
        assert!(residual.is_finite());
        assert!(engine.sigma2() > 0.0);
    }
}
