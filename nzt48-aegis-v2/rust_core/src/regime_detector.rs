//! Regime detection via jump-diffusion and Hurst exponent estimation.
//! Used to classify market conditions and prevent entry during flash crashes.
//! Jump-Diffusion (50 LOC) + Hurst Exponent (120 LOC) + Main Detector (20 LOC)

// ============================================================================
// Jump-Diffusion Detection (50 LOC)
// ============================================================================

/// Detects flash crashes / gap moves that could sweep stops.
/// Prevents entry during extreme price moves combined with extreme volatility.
pub struct JumpDiffusionDetector {
    rvol_threshold: f64,        // 3.5 standard (realized vol explosion)
    price_change_threshold_atr_multiplier: f64, // 2.0 standard (move size in ATR units)
}

impl JumpDiffusionDetector {
    /// Create detector with default thresholds (RVOL=3.5, ATR_multiplier=2.0)
    pub fn new() -> Self {
        Self {
            rvol_threshold: 3.5,
            price_change_threshold_atr_multiplier: 2.0,
        }
    }

    /// Customize thresholds for specific market conditions
    pub fn with_thresholds(rvol_thresh: f64, multiplier: f64) -> Self {
        Self {
            rvol_threshold: rvol_thresh,
            price_change_threshold_atr_multiplier: multiplier,
        }
    }

    /// Returns true if jump-diffusion signature detected (blocks entry).
    /// Logic: BOTH rvol > threshold AND price_move > multiplier * atr
    /// This prevents entry during flash crashes, gap moves, or news shocks.
    pub fn detect_jump(&self, rvol: f64, atr: f64, price_move: f64) -> bool {
        rvol > self.rvol_threshold
            && price_move > self.price_change_threshold_atr_multiplier * atr
    }

    /// Get current RVOL threshold
    pub fn rvol_threshold(&self) -> f64 {
        self.rvol_threshold
    }

    /// Get current price change multiplier
    pub fn price_multiplier(&self) -> f64 {
        self.price_change_threshold_atr_multiplier
    }
}

impl Default for JumpDiffusionDetector {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod jump_tests {
    use super::*;

    #[test]
    fn test_jump_detected_both_conditions() {
        let detector = JumpDiffusionDetector::new();
        // RVOL=4.0 > 3.5 ✓, price_move=1.5 > 2*0.5=1.0 ✓ → jump detected
        assert!(detector.detect_jump(4.0, 0.5, 1.5));
    }

    #[test]
    fn test_jump_not_detected_low_rvol() {
        let detector = JumpDiffusionDetector::new();
        // RVOL=2.0 < 3.5 ✗ → no jump
        assert!(!detector.detect_jump(2.0, 0.5, 1.5));
    }

    #[test]
    fn test_jump_not_detected_small_move() {
        let detector = JumpDiffusionDetector::new();
        // RVOL=4.0 > 3.5 ✓, but price_move=0.8 < 2*0.5=1.0 ✗ → no jump
        assert!(!detector.detect_jump(4.0, 0.5, 0.8));
    }

    #[test]
    fn test_custom_thresholds() {
        let detector = JumpDiffusionDetector::with_thresholds(2.5, 3.0);
        // RVOL=3.0 > 2.5 ✓, price_move=3.5 > 3*1.0=3.0 ✓ → jump detected
        assert!(detector.detect_jump(3.0, 1.0, 3.5));
        // RVOL=2.0 < 2.5 ✗ → no jump
        assert!(!detector.detect_jump(2.0, 1.0, 3.5));
    }
}

// ============================================================================
// Hurst Exponent (120 LOC)
// ============================================================================

/// Estimates Hurst exponent via rescaled range (R/S) analysis.
/// H > 0.5 = trending (persistent, momentum advantage)
/// H < 0.5 = mean-reverting (contrarian advantage)
/// H ≈ 0.5 = random walk (no edge)
pub struct HurstEstimator {
    window_size: usize,  // 30 bars standard for intraday
}

impl HurstEstimator {
    /// Create estimator with default 30-bar window
    pub fn new() -> Self {
        Self { window_size: 30 }
    }

    /// Create estimator with custom window size
    pub fn with_window(size: usize) -> Self {
        Self { window_size: size }
    }

    /// Calculate Hurst exponent from price series.
    /// Returns: Option<f64> ∈ [0.0, 1.0]
    /// Returns None if insufficient data (< window_size bars).
    pub fn estimate_hurst(&self, prices: &[f64]) -> Option<f64> {
        if prices.len() < self.window_size {
            return None;
        }

        // Calculate log returns from prices
        let mut returns = Vec::with_capacity(prices.len() - 1);
        for i in 1..prices.len() {
            if prices[i - 1] <= 0.0 {
                return None; // Invalid price series
            }
            let ret = (prices[i] / prices[i - 1]).ln();
            returns.push(ret);
        }

        // Calculate mean return for this window
        let mean_return = returns.iter().sum::<f64>() / returns.len() as f64;

        // Collect R/S values at multiple lag periods
        let mut rs_values = Vec::new();

        for lag in 2..=self.window_size.min(returns.len()) {
            // Calculate range: max - min of cumulative deviations
            let mut deviation: f64 = 0.0;
            let mut max_dev: f64 = 0.0;
            let mut min_dev: f64 = 0.0;

            for i in 0..lag {
                deviation += returns[i] - mean_return;
                max_dev = max_dev.max(deviation);
                min_dev = min_dev.min(deviation);
            }

            let range = max_dev - min_dev;

            // Calculate standard deviation over this lag
            let sum_sq: f64 = returns[..lag]
                .iter()
                .map(|r| (r - mean_return).powi(2))
                .sum();
            let variance = sum_sq / lag as f64;
            let std_dev = variance.sqrt().max(1e-10); // Avoid division by zero

            // Calculate R/S ratio
            let rs = (range / std_dev).max(1e-10);
            rs_values.push((lag as f64, rs.ln()));
        }

        if rs_values.len() < 2 {
            return None;
        }

        // Linear regression of log(R/S) vs log(lag) to extract slope (Hurst exponent)
        let n = rs_values.len() as f64;
        let mut sum_x = 0.0;
        let mut sum_y = 0.0;
        let mut sum_xy = 0.0;
        let mut sum_x2 = 0.0;

        for (lag, rs_ln) in rs_values.iter() {
            let x = lag.ln();
            sum_x += x;
            sum_y += rs_ln;
            sum_xy += x * rs_ln;
            sum_x2 += x * x;
        }

        let numerator = n * sum_xy - sum_x * sum_y;
        let denominator = n * sum_x2 - sum_x * sum_x;

        if denominator.abs() < 1e-10 {
            return None; // Singular regression
        }

        let slope = numerator / denominator;
        Some(slope.clamp(0.0, 1.0))
    }

    /// Classify regime by Hurst exponent
    pub fn classify_regime(&self, prices: &[f64]) -> RegimeClass {
        match self.estimate_hurst(prices) {
            Some(h) if h > 0.55 => RegimeClass::Trending,
            Some(h) if h < 0.45 => RegimeClass::MeanReverting,
            _ => RegimeClass::Random,
        }
    }
}

impl Default for HurstEstimator {
    fn default() -> Self {
        Self::new()
    }
}

/// Market regime classification based on Hurst exponent
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RegimeClass {
    /// H > 0.55: Trending, persistent. Momentum strategies work.
    Trending,
    /// H < 0.45: Mean-reverting. Contrarian strategies work.
    MeanReverting,
    /// 0.45 <= H <= 0.55: Random walk. No directional edge.
    Random,
}

#[cfg(test)]
mod hurst_tests {
    use super::*;

    #[test]
    fn test_hurst_trending_series() {
        // Strong uptrend: prices steadily increasing
        let prices: Vec<f64> = (0..50).map(|i| 100.0 + i as f64).collect();
        let estimator = HurstEstimator::new();
        if let Some(h) = estimator.estimate_hurst(&prices) {
            assert!(h > 0.5, "Uptrend should have H > 0.5, got {}", h);
        }
    }

    #[test]
    fn test_hurst_mean_reverting_series() {
        // Oscillating series: mean-reverting behavior
        let mut prices = vec![];
        for i in 0..50 {
            let p = 100.0 + ((i % 10) as f64 - 5.0);
            prices.push(p);
        }
        let estimator = HurstEstimator::new();
        if let Some(h) = estimator.estimate_hurst(&prices) {
            assert!(h < 0.6, "Oscillating should have H < 0.6, got {}", h);
        }
    }

    #[test]
    fn test_hurst_insufficient_data() {
        let prices = vec![100.0, 101.0];
        let estimator = HurstEstimator::new();
        assert!(estimator.estimate_hurst(&prices).is_none());
    }

    #[test]
    fn test_hurst_custom_window() {
        let prices: Vec<f64> = (0..20).map(|i| 100.0 + i as f64 * 0.5).collect();
        let estimator = HurstEstimator::with_window(10);
        let result = estimator.estimate_hurst(&prices);
        // Should succeed with smaller window
        assert!(result.is_some());
    }

    #[test]
    fn test_regime_classification_trending() {
        let prices: Vec<f64> = (0..50).map(|i| 100.0 + i as f64).collect();
        let estimator = HurstEstimator::new();
        let regime = estimator.classify_regime(&prices);
        // Strong trend should classify as trending or random
        assert!(
            regime == RegimeClass::Trending || regime == RegimeClass::Random,
            "Trend should be Trending or Random, got {:?}",
            regime
        );
    }

    #[test]
    fn test_regime_classification_mean_revert() {
        let mut prices = vec![];
        for i in 0..50 {
            let p = 100.0 + ((i % 10) as f64 - 5.0);
            prices.push(p);
        }
        let estimator = HurstEstimator::new();
        let regime = estimator.classify_regime(&prices);
        // Oscillation should classify as mean-reverting or random
        assert!(
            regime == RegimeClass::MeanReverting || regime == RegimeClass::Random,
            "Oscillation should be MeanReverting or Random, got {:?}",
            regime
        );
    }

    #[test]
    fn test_hurst_invalid_prices() {
        let prices = vec![100.0, 0.0, 101.0]; // Contains zero
        let estimator = HurstEstimator::new();
        assert!(estimator.estimate_hurst(&prices).is_none());
    }
}

// ============================================================================
// Regime Detector (Main Struct, 20 LOC)
// ============================================================================

/// Combined regime detector using jump-diffusion + Hurst exponent.
/// Evaluates market conditions for entry approval.
pub struct RegimeDetector {
    jump_detector: JumpDiffusionDetector,
    hurst_estimator: HurstEstimator,
}

impl RegimeDetector {
    /// Create detector with default thresholds
    pub fn new() -> Self {
        Self {
            jump_detector: JumpDiffusionDetector::new(),
            hurst_estimator: HurstEstimator::new(),
        }
    }

    /// Evaluate market regime from all signals
    /// Returns decision with jump detection and Hurst classification
    pub fn evaluate(
        &self,
        rvol: f64,
        atr: f64,
        price_move: f64,
        prices: &[f64],
    ) -> RegimeDecision {
        let has_jump = self.jump_detector.detect_jump(rvol, atr, price_move);
        let hurst_regime = self.hurst_estimator.classify_regime(prices);

        // Confidence = 90% if clean, 75% if regime clear but jump near
        let confidence = if has_jump { 75.0 } else { 90.0 };

        RegimeDecision {
            has_jump,
            hurst_regime,
            confidence,
        }
    }
}

impl Default for RegimeDetector {
    fn default() -> Self {
        Self::new()
    }
}

/// Result of regime evaluation
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RegimeDecision {
    /// True if jump-diffusion detected (prevents entry)
    pub has_jump: bool,
    /// Market regime via Hurst analysis
    pub hurst_regime: RegimeClass,
    /// Confidence in regime assessment [0.0, 100.0]
    pub confidence: f64,
}

#[cfg(test)]
mod detector_tests {
    use super::*;

    #[test]
    fn test_regime_detector_clean_trending() {
        let detector = RegimeDetector::new();
        let prices: Vec<f64> = (0..50).map(|i| 100.0 + i as f64).collect();
        let decision = detector.evaluate(1.5, 0.5, 0.3, &prices);
        assert!(!decision.has_jump);
        assert_eq!(decision.confidence, 90.0);
    }

    #[test]
    fn test_regime_detector_with_jump() {
        let detector = RegimeDetector::new();
        let prices: Vec<f64> = (0..50).map(|i| 100.0 + i as f64).collect();
        let decision = detector.evaluate(4.0, 0.5, 1.5, &prices);
        assert!(decision.has_jump);
        assert_eq!(decision.confidence, 75.0);
    }

    #[test]
    fn test_regime_detector_insufficient_price_data() {
        let detector = RegimeDetector::new();
        let prices = vec![100.0];
        let decision = detector.evaluate(2.0, 0.5, 0.5, &prices);
        // Should handle gracefully
        assert_eq!(decision.hurst_regime, RegimeClass::Random);
    }
}
