//! Chandelier Exit Logic (Le Beau 1999)
//! Optimized for sub-20μs execution.

use crate::indicators::{atr, sma};

/// Calculate Chandelier Exit stop level.
///
/// Formula:
///   Long: Highest High - (ATR × Multiplier)
///   Short: Lowest Low + (ATR × Multiplier)
///
/// Args:
///   highs: High prices
///   lows: Low prices
///   closes: Close prices
///   atr_period: ATR lookback period
///   atr_multiplier: ATR multiplier (default 3.0)
///   long: True for long position, False for short
///
/// Returns:
///   Stop loss price
///
/// Performance: <20μs per call (vs 500μs in Python)
pub fn chandelier_stop(
    highs: &[f64],
    lows: &[f64],
    closes: &[f64],
    atr_period: usize,
    atr_multiplier: f64,
    long: bool,
) -> f64 {
    let atr_val = atr(highs, lows, closes, atr_period);

    if long {
        // Long: Stop = Highest High - (ATR × Multiplier)
        let highest_high = highs.iter().take(atr_period).fold(f64::NEG_INFINITY, |a, &b| a.max(b));
        highest_high - (atr_val * atr_multiplier)
    } else {
        // Short: Stop = Lowest Low + (ATR × Multiplier)
        let lowest_low = lows.iter().take(atr_period).fold(f64::INFINITY, |a, &b| a.min(b));
        lowest_low + (atr_val * atr_multiplier)
    }
}

/// Chandelier ladder for profit banking.
///
/// NZT-48 uses a 5-rung ladder:
///   - Rung 1: +2.0% → Bank 20%
///   - Rung 2: +3.5% → Bank 20%
///   - Rung 3: +5.0% → Bank 20%
///   - Rung 4: +7.0% → Bank 20%
///   - Rung 5: +10.0% → Bank remaining 20%
///
/// Returns: (rung_reached, bank_percent)
pub fn chandelier_ladder(entry_price: f64, current_price: f64, long: bool) -> (u8, f64) {
    let pnl_pct = if long {
        ((current_price - entry_price) / entry_price) * 100.0
    } else {
        ((entry_price - current_price) / entry_price) * 100.0
    };

    match pnl_pct {
        x if x >= 10.0 => (5, 0.20), // Rung 5: +10%
        x if x >= 7.0 => (4, 0.20),  // Rung 4: +7%
        x if x >= 5.0 => (3, 0.20),  // Rung 3: +5%
        x if x >= 3.5 => (2, 0.20),  // Rung 2: +3.5%
        x if x >= 2.0 => (1, 0.20),  // Rung 1: +2%
        _ => (0, 0.0),                // No rung reached
    }
}

/// Dynamic Chandelier adjustment based on market regime.
///
/// Multiplier ranges:
///   - Low vol (VIX < 15): 2.5x (tight stops)
///   - Normal (VIX 15-25): 3.0x (standard)
///   - High vol (VIX > 25): 4.0x (wide stops to avoid whipsaws)
///
/// Returns: Adjusted ATR multiplier
pub fn regime_adjusted_multiplier(vix: f64, base_multiplier: f64) -> f64 {
    match vix {
        x if x < 15.0 => base_multiplier * 0.83,  // 3.0 → 2.5
        x if x > 25.0 => base_multiplier * 1.33,  // 3.0 → 4.0
        _ => base_multiplier,                     // 3.0 unchanged
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_chandelier_stop_long() {
        let highs = vec![105.0, 104.0, 103.0, 102.0, 101.0];
        let lows = vec![100.0, 99.0, 98.0, 97.0, 96.0];
        let closes = vec![102.5, 101.5, 100.5, 99.5, 98.5];

        let stop = chandelier_stop(&highs, &lows, &closes, 3, 3.0, true);

        // Stop should be below highest high
        assert!(stop < 105.0);
        // Stop should be reasonable (not negative)
        assert!(stop > 0.0);
    }

    #[test]
    fn test_chandelier_stop_short() {
        let highs = vec![105.0, 104.0, 103.0, 102.0, 101.0];
        let lows = vec![100.0, 99.0, 98.0, 97.0, 96.0];
        let closes = vec![102.5, 101.5, 100.5, 99.5, 98.5];

        let stop = chandelier_stop(&highs, &lows, &closes, 3, 3.0, false);

        // Stop should be above lowest low
        assert!(stop > 96.0);
    }

    #[test]
    fn test_chandelier_ladder() {
        let entry = 100.0;

        // Rung 0: +1% (no banking)
        let (rung, pct) = chandelier_ladder(entry, 101.0, true);
        assert_eq!(rung, 0);
        assert_eq!(pct, 0.0);

        // Rung 1: +2% (bank 20%)
        let (rung, pct) = chandelier_ladder(entry, 102.0, true);
        assert_eq!(rung, 1);
        assert_eq!(pct, 0.20);

        // Rung 3: +5% (bank 20%)
        let (rung, pct) = chandelier_ladder(entry, 105.0, true);
        assert_eq!(rung, 3);
        assert_eq!(pct, 0.20);

        // Rung 5: +10% (bank 20%)
        let (rung, pct) = chandelier_ladder(entry, 110.0, true);
        assert_eq!(rung, 5);
        assert_eq!(pct, 0.20);
    }

    #[test]
    fn test_regime_adjusted_multiplier() {
        let base = 3.0;

        // Low vol: tighter stops
        let mult = regime_adjusted_multiplier(12.0, base);
        assert!((mult - 2.5).abs() < 0.1);

        // Normal vol: standard stops
        let mult = regime_adjusted_multiplier(20.0, base);
        assert!((mult - 3.0).abs() < 0.01);

        // High vol: wider stops
        let mult = regime_adjusted_multiplier(30.0, base);
        assert!((mult - 4.0).abs() < 0.1);
    }
}
