//! Cross-Asset Macro Integration (Phase 9)
//!
//! Monitors VIX, DXY, credit spreads, and Fear & Greed to classify
//! the current macro regime and gate position-sizing accordingly.

/// Snapshot of macro-level indicators used for regime classification.
#[derive(Debug, Clone, Copy)]
pub struct MacroIndicator {
    pub vix: f64,
    pub dxy: f64,
    pub credit_spread_bps: f64,
    pub fear_greed: f64,
    pub last_update_ns: u64,
}

impl Default for MacroIndicator {
    fn default() -> Self {
        Self {
            vix: 15.0,
            dxy: 100.0,
            credit_spread_bps: 100.0,
            fear_greed: 50.0,
            last_update_ns: 0,
        }
    }
}

/// Regime signal derived from macro indicators.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MacroRegimeSignal {
    Normal,
    Caution,
    Stress,
    Crisis,
}

/// Cross-asset macro regime evaluator.
///
/// Feed it live VIX, DXY, credit-spread, and Fear & Greed values;
/// call [`evaluate`](Self::evaluate) to obtain the current regime.
#[derive(Debug, Clone)]
pub struct CrossAssetMacro {
    indicator: MacroIndicator,
}

impl Default for CrossAssetMacro {
    fn default() -> Self {
        Self::new()
    }
}

impl CrossAssetMacro {
    /// Create a new instance with default (benign) indicator values.
    pub fn new() -> Self {
        Self {
            indicator: MacroIndicator::default(),
        }
    }

    /// Create from an existing indicator snapshot (Phase 9 helper).
    pub fn from_indicator(indicator: MacroIndicator) -> Self {
        Self { indicator }
    }

    /// Return a reference to the current indicator snapshot.
    pub fn indicator(&self) -> &MacroIndicator {
        &self.indicator
    }

    pub fn update_vix(&mut self, value: f64, now_ns: u64) {
        self.indicator.vix = value;
        self.indicator.last_update_ns = now_ns;
    }

    pub fn update_dxy(&mut self, value: f64, now_ns: u64) {
        self.indicator.dxy = value;
        self.indicator.last_update_ns = now_ns;
    }

    pub fn update_credit(&mut self, value: f64, now_ns: u64) {
        self.indicator.credit_spread_bps = value;
        self.indicator.last_update_ns = now_ns;
    }

    pub fn update_fear_greed(&mut self, value: f64, now_ns: u64) {
        self.indicator.fear_greed = value;
        self.indicator.last_update_ns = now_ns;
    }

    /// Evaluate the current macro regime.
    ///
    /// Thresholds (evaluated in order, first match wins):
    /// - **Crisis**: VIX > 30 OR credit spread > 200 bps
    /// - **Stress**: VIX > 25 OR credit spread > 150 bps OR Fear & Greed < 25
    /// - **Caution**: VIX > 20 OR Fear & Greed < 40
    /// - **Normal**: everything else
    pub fn evaluate(&self) -> MacroRegimeSignal {
        let i = &self.indicator;

        if i.vix > 30.0 || i.credit_spread_bps > 200.0 {
            return MacroRegimeSignal::Crisis;
        }
        if i.vix > 25.0 || i.credit_spread_bps > 150.0 || i.fear_greed < 25.0 {
            return MacroRegimeSignal::Stress;
        }
        if i.vix > 20.0 || i.fear_greed < 40.0 {
            return MacroRegimeSignal::Caution;
        }
        MacroRegimeSignal::Normal
    }

    /// Returns `true` when the latest update is older than `threshold_secs`.
    pub fn is_stale(&self, now_ns: u64, threshold_secs: u64) -> bool {
        let threshold_ns = threshold_secs.saturating_mul(1_000_000_000);
        now_ns.saturating_sub(self.indicator.last_update_ns) > threshold_ns
    }

    /// Returns `true` if the current regime is `Stress` or `Crisis`.
    pub fn should_escalate_regime(&self) -> bool {
        matches!(
            self.evaluate(),
            MacroRegimeSignal::Stress | MacroRegimeSignal::Crisis
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_regime_is_normal() {
        let cam = CrossAssetMacro::new();
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Normal);
        assert!(!cam.should_escalate_regime());
    }

    #[test]
    fn high_vix_triggers_crisis() {
        let mut cam = CrossAssetMacro::new();
        cam.update_vix(31.0, 1_000_000_000);
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Crisis);
        assert!(cam.should_escalate_regime());
    }

    #[test]
    fn wide_credit_triggers_crisis() {
        let mut cam = CrossAssetMacro::new();
        cam.update_credit(201.0, 2_000_000_000);
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Crisis);
    }

    #[test]
    fn moderate_vix_triggers_stress() {
        let mut cam = CrossAssetMacro::new();
        cam.update_vix(26.0, 3_000_000_000);
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Stress);
        assert!(cam.should_escalate_regime());
    }

    #[test]
    fn low_fear_greed_triggers_stress() {
        let mut cam = CrossAssetMacro::new();
        cam.update_fear_greed(20.0, 4_000_000_000);
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Stress);
    }

    #[test]
    fn caution_on_slightly_elevated_vix() {
        let mut cam = CrossAssetMacro::new();
        cam.update_vix(21.0, 5_000_000_000);
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Caution);
        assert!(!cam.should_escalate_regime());
    }

    #[test]
    fn caution_on_fear_greed_below_40() {
        let mut cam = CrossAssetMacro::new();
        cam.update_fear_greed(35.0, 6_000_000_000);
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Caution);
    }

    #[test]
    fn staleness_check() {
        let mut cam = CrossAssetMacro::new();
        cam.update_vix(18.0, 1_000_000_000); // 1 second in ns
        // 60 seconds later, threshold = 30 seconds → stale
        assert!(cam.is_stale(61_000_000_000, 30));
        // 10 seconds later, threshold = 30 seconds → not stale
        assert!(!cam.is_stale(11_000_000_000, 30));
    }

    #[test]
    fn crisis_takes_precedence_over_stress() {
        let mut cam = CrossAssetMacro::new();
        // Both crisis and stress conditions met
        cam.update_vix(35.0, 1_000_000_000);
        cam.update_fear_greed(10.0, 1_000_000_000);
        assert_eq!(cam.evaluate(), MacroRegimeSignal::Crisis);
    }

    #[test]
    fn last_update_tracks_latest_call() {
        let mut cam = CrossAssetMacro::new();
        cam.update_vix(18.0, 100);
        cam.update_dxy(105.0, 200);
        assert_eq!(cam.indicator().last_update_ns, 200);
    }
}
