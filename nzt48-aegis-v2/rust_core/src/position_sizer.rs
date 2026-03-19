//! Position Sizer - Dynamic Kelly + Confidence Adjustment + Tier-Based Stops
//! 210 LOC total: Kelly math, confidence scaling, stop widths, position limits

use crate::entry_engine::Tier;

// ============================================================================
// Kelly Criterion Calculator (80 LOC)
// ============================================================================

pub struct KellyCalculator {
    fractional_kelly: f64,  // 0.25 = conservative 25% Kelly
}

impl KellyCalculator {
    pub fn new() -> Self {
        Self {
            fractional_kelly: 0.25,
        }
    }

    pub fn with_fraction(fraction: f64) -> Self {
        Self {
            fractional_kelly: fraction.clamp(0.0, 1.0),
        }
    }

    /// Calculate raw Kelly percentage
    /// Formula: kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_loss
    /// Returns: f64 ∈ [0.0, 1.0]
    pub fn calculate_raw_kelly(
        &self,
        win_rate: f64,
        avg_win: f64,
        avg_loss: f64,
    ) -> f64 {
        if avg_loss <= 0.0 {
            return 0.0;  // Guard: no division by zero
        }

        let prob_win = win_rate.clamp(0.0, 1.0);
        let prob_loss = 1.0 - prob_win;

        let kelly = (prob_win * avg_win - prob_loss * avg_loss) / avg_loss;
        kelly.clamp(0.0, 1.0)
    }

    /// Apply fractional Kelly (conservative scaling)
    pub fn fractional_kelly(&self, raw_kelly: f64) -> f64 {
        raw_kelly * self.fractional_kelly
    }

    /// Full pipeline: raw → fractional
    pub fn kelly_to_position_fraction(
        &self,
        win_rate: f64,
        avg_win: f64,
        avg_loss: f64,
    ) -> f64 {
        let raw = self.calculate_raw_kelly(win_rate, avg_win, avg_loss);
        self.fractional_kelly(raw)
    }
}

impl Default for KellyCalculator {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod kelly_tests {
    use super::*;

    #[test]
    fn test_kelly_calculation() {
        let calc = KellyCalculator::new();
        // WR 60%, Avg Win £100, Avg Loss £80
        // Formula: (0.6 * 100 - 0.4 * 80) / 80 = (60 - 32) / 80 = 28/80 = 0.35
        let raw = calc.calculate_raw_kelly(0.60, 100.0, 80.0);
        assert!((raw - 0.35).abs() < 0.01);  // ~35%
        let frac = calc.fractional_kelly(raw);
        assert!((frac - 0.0875).abs() < 0.001);  // ~8.75% (25% of 35%)
    }

    #[test]
    fn test_kelly_zero_loss() {
        let calc = KellyCalculator::new();
        let kelly = calc.calculate_raw_kelly(0.5, 100.0, 0.0);
        assert_eq!(kelly, 0.0);  // Guarded against div by zero
    }

    #[test]
    fn test_kelly_100_win_rate() {
        let calc = KellyCalculator::new();
        let kelly = calc.calculate_raw_kelly(1.0, 100.0, 80.0);
        assert_eq!(kelly, 1.0);  // 100% = clamped to 1.0
    }

    #[test]
    fn test_kelly_negative_expectancy() {
        let calc = KellyCalculator::new();
        // WR 40%, Avg Win £100, Avg Loss £80
        // Formula: (0.4 * 100 - 0.6 * 80) / 80 = (40 - 48) / 80 = -8/80 = -0.1 (negative)
        // But calculate_raw_kelly clamps to [0.0, 1.0], so negative values are clamped to 0.0
        let kelly = calc.calculate_raw_kelly(0.40, 100.0, 80.0);  // Losing strategy
        assert_eq!(kelly, 0.0);  // Clamped to 0 in calculate_raw_kelly
    }

    #[test]
    fn test_fractional_scaling() {
        let calc = KellyCalculator::with_fraction(0.5);  // 50% Kelly
        let raw = 0.20;
        let frac = calc.fractional_kelly(raw);
        assert_eq!(frac, 0.10);  // 50% of 20%
    }
}

// ============================================================================
// Confidence Adjustment (30 LOC)
// ============================================================================

pub struct ConfidenceScaler;

impl ConfidenceScaler {
    /// Scale position size by entry confidence
    /// Logic: final_shares = kelly_shares * (confidence_pct / 100.0)
    pub fn adjust_for_confidence(
        kelly_shares: u32,
        confidence_pct: f64,
    ) -> u32 {
        let confidence_factor = (confidence_pct / 100.0).clamp(0.0, 1.0);
        let adjusted = kelly_shares as f64 * confidence_factor;
        adjusted.floor() as u32  // floor() ensures whole shares
    }

    /// Multi-factor adjustment
    pub fn multi_factor_adjust(
        kelly_shares: u32,
        entry_confidence: f64,      // Entry type confidence (65-82%)
        regime_confidence: f64,     // Market regime confidence (50-100%)
        volatility_confidence: f64, // Volatility regime confidence
    ) -> u32 {
        let base_factor = entry_confidence / 100.0;
        let regime_factor = regime_confidence / 100.0;
        let vol_factor = volatility_confidence / 100.0;

        let combined = base_factor * regime_factor * vol_factor;
        let adjusted = kelly_shares as f64 * combined;
        adjusted.floor() as u32
    }
}

#[cfg(test)]
mod confidence_tests {
    use super::*;

    #[test]
    fn test_simple_confidence_scaling() {
        // kelly: 150 shares, confidence: 80%
        let result = ConfidenceScaler::adjust_for_confidence(150, 80.0);
        assert_eq!(result, 120);  // 150 * 0.8 = 120
    }

    #[test]
    fn test_confidence_scaling_with_floor() {
        // kelly: 100 shares, confidence: 33.3%
        let result = ConfidenceScaler::adjust_for_confidence(100, 33.3);
        assert_eq!(result, 33);  // floor(100 * 0.333) = 33
    }

    #[test]
    fn test_multi_factor_adjustment() {
        // entry: 80%, regime: 75%, vol: 90%
        let result = ConfidenceScaler::multi_factor_adjust(200, 80.0, 75.0, 90.0);
        assert_eq!(result, (200 as f64 * 0.8 * 0.75 * 0.9).floor() as u32);
    }
}

// ============================================================================
// Tier-Based Stop Widths (60 LOC)
// ============================================================================

pub struct StopWidthCalculator;

impl StopWidthCalculator {
    /// Calculate stop loss price based on tier
    /// Tier 1: 1.5×ATR (safest, widest)
    /// Tier 2: 1.2×ATR (moderate)
    /// Tier 3: 1.0×ATR (aggressive, tightest)
    pub fn calculate_stop_price(
        entry_price: f64,
        atr: f64,
        tier: Tier,
    ) -> f64 {
        let multiplier = match tier {
            Tier::One => 1.5,
            Tier::Two => 1.2,
            Tier::Three => 1.0,
            Tier::Four => 0.0,  // No trading
        };

        entry_price - (multiplier * atr)
    }

    /// Calculate stop as percentage from entry
    pub fn calculate_stop_pct(
        atr: f64,
        entry_price: f64,
        tier: Tier,
    ) -> f64 {
        let stop_price = Self::calculate_stop_price(entry_price, atr, tier);
        ((entry_price - stop_price) / entry_price) * 100.0
    }

    /// Validate stop is reasonable (not too close or too far)
    pub fn validate_stop(
        entry_price: f64,
        stop_price: f64,
        min_pct: f64,
        max_pct: f64,
    ) -> bool {
        let stop_pct = ((entry_price - stop_price) / entry_price).abs() * 100.0;
        stop_pct >= min_pct && stop_pct <= max_pct
    }
}

#[cfg(test)]
mod stop_tests {
    use super::*;

    #[test]
    fn test_tier_one_stop() {
        let stop = StopWidthCalculator::calculate_stop_price(100.0, 2.0, Tier::One);
        assert_eq!(stop, 97.0);  // 100 - 1.5*2
    }

    #[test]
    fn test_tier_two_stop() {
        let stop = StopWidthCalculator::calculate_stop_price(100.0, 2.0, Tier::Two);
        assert_eq!(stop, 97.6);  // 100 - 1.2*2
    }

    #[test]
    fn test_tier_three_stop() {
        let stop = StopWidthCalculator::calculate_stop_price(100.0, 2.0, Tier::Three);
        assert_eq!(stop, 98.0);  // 100 - 1.0*2
    }

    #[test]
    fn test_tier_four_no_trading() {
        let stop = StopWidthCalculator::calculate_stop_price(100.0, 2.0, Tier::Four);
        assert_eq!(stop, 100.0);  // 100 - 0*2
    }

    #[test]
    fn test_stop_percentage() {
        let pct = StopWidthCalculator::calculate_stop_pct(2.0, 100.0, Tier::One);
        assert!((pct - 3.0).abs() < 0.01);  // ~3%
    }

    #[test]
    fn test_stop_validation() {
        let valid = StopWidthCalculator::validate_stop(100.0, 97.0, 2.0, 5.0);
        assert!(valid);  // 3% stop is within 2-5% range

        let invalid = StopWidthCalculator::validate_stop(100.0, 94.0, 2.0, 5.0);
        assert!(!invalid);  // 6% stop is outside range (too wide)
    }
}

// ============================================================================
// Tier-Based Position Limits (40 LOC)
// ============================================================================

pub struct PositionLimiter;

impl PositionLimiter {
    /// Maximum position size as % of account by tier
    /// Tier 1: 6% (aggressive, leveraged ETPs)
    /// Tier 2: 4% (moderate)
    /// Tier 3: 3% (conservative)
    pub fn max_position_pct(tier: Tier) -> f64 {
        match tier {
            Tier::One => 0.06,
            Tier::Two => 0.04,
            Tier::Three => 0.03,
            Tier::Four => 0.0,
        }
    }

    /// Calculate position size in shares
    /// shares = account_equity * max_pct * kelly_adjusted
    pub fn calculate_position_size(
        account_equity: f64,
        tier: Tier,
        kelly_fraction: f64,
    ) -> u32 {
        let max_pct = Self::max_position_pct(tier);
        let notional = account_equity * max_pct * kelly_fraction;
        notional.floor() as u32
    }

    /// Validate position doesn't exceed limits
    pub fn validate_position(
        account_equity: f64,
        tier: Tier,
        position_size: u32,
        entry_price: f64,
    ) -> bool {
        let notional = position_size as f64 * entry_price;
        let max_notional = account_equity * Self::max_position_pct(tier);
        notional <= max_notional
    }
}

#[cfg(test)]
mod limiter_tests {
    use super::*;

    #[test]
    fn test_tier_one_limit() {
        assert_eq!(PositionLimiter::max_position_pct(Tier::One), 0.06);
    }

    #[test]
    fn test_tier_three_limit() {
        assert_eq!(PositionLimiter::max_position_pct(Tier::Three), 0.03);
    }

    #[test]
    fn test_position_size_calculation() {
        // Account: £10K, Tier Two (4%), Kelly 0.05
        let size = PositionLimiter::calculate_position_size(10000.0, Tier::Two, 0.05);
        assert_eq!(size, 20);  // floor(10000 * 0.04 * 0.05)
    }

    #[test]
    fn test_position_validation() {
        // Account: £10K, Tier One, max 6% = £600
        let valid = PositionLimiter::validate_position(10000.0, Tier::One, 6, 100.0);
        assert!(valid);  // 6 shares * £100 = £600

        let invalid = PositionLimiter::validate_position(10000.0, Tier::One, 7, 100.0);
        assert!(!invalid);  // 7 shares * £100 = £700 > £600
    }
}
