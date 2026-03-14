//! Phase 21: Cross-timezone intelligence.
//! Aggregates Asian sentiment and carry risk for European/UK session decisions.

/// Aggregated cross-timezone intelligence snapshot.
#[derive(Clone, Debug)]
pub struct TimezoneIntel {
    /// Asian session sentiment (-1.0 bearish … +1.0 bullish).
    pub asian_sentiment: f64,
    /// European session sentiment (-1.0 bearish … +1.0 bullish).
    pub european_sentiment: f64,
    /// Carry risk score (0 = no risk … 100 = extreme risk).
    pub carry_risk_score: f64,
    /// Nanosecond timestamp of last update.
    pub last_update_ns: u64,
}

impl Default for TimezoneIntel {
    fn default() -> Self {
        Self {
            asian_sentiment: 0.0,
            european_sentiment: 0.0,
            carry_risk_score: 0.0,
            last_update_ns: 0,
        }
    }
}

/// Cross-timezone intelligence engine.
/// Computes sentiment from Asian session outcomes and carry risk
/// to inform European/UK session position sizing and entry decisions.
#[derive(Clone, Debug)]
pub struct CrossTimezoneEngine {
    intel: TimezoneIntel,
    /// Threshold above which exposure should be reduced (carry_risk_score).
    risk_threshold: f64,
    /// Sentiment decay factor applied each update (0.0–1.0).
    decay: f64,
}

impl CrossTimezoneEngine {
    /// Create a new engine with default risk threshold of 70 and decay of 0.8.
    pub fn new() -> Self {
        Self {
            intel: TimezoneIntel::default(),
            risk_threshold: 70.0,
            decay: 0.8,
        }
    }

    /// Create with custom risk threshold and decay.
    pub fn with_params(risk_threshold: f64, decay: f64) -> Self {
        Self {
            intel: TimezoneIntel::default(),
            risk_threshold: risk_threshold.clamp(0.0, 100.0),
            decay: decay.clamp(0.0, 1.0),
        }
    }

    /// Update Asian session sentiment from close data.
    /// `session_return`: aggregate return of Asian session (e.g. -0.02 = -2%).
    /// `winner_count`: number of positive-returning instruments.
    /// `loser_count`: number of negative-returning instruments.
    pub fn update_asian_close(
        &mut self,
        session_return: f64,
        winner_count: u32,
        loser_count: u32,
        now_ns: u64,
    ) {
        let total = winner_count + loser_count;
        let breadth = if total > 0 {
            (winner_count as f64 - loser_count as f64) / total as f64
        } else {
            0.0
        };
        // Blend return signal (60%) with breadth (40%), apply decay to prior.
        let raw = session_return.clamp(-0.10, 0.10) * 10.0 * 0.6 + breadth * 0.4;
        self.intel.asian_sentiment =
            (self.intel.asian_sentiment * self.decay + raw * (1.0 - self.decay)).clamp(-1.0, 1.0);
        self.intel.last_update_ns = now_ns;
    }

    /// Update European session sentiment from opening gap data.
    /// `gap_pct`: opening gap as a fraction (e.g. 0.01 = +1% gap up).
    pub fn update_european_open(&mut self, gap_pct: f64, now_ns: u64) {
        let raw = gap_pct.clamp(-0.05, 0.05) * 20.0; // scale to [-1, 1]
        self.intel.european_sentiment =
            (self.intel.european_sentiment * self.decay + raw * (1.0 - self.decay)).clamp(-1.0, 1.0);
        self.intel.last_update_ns = now_ns;
    }

    /// Compute carry risk score from overnight position exposure.
    /// `carry_count`: number of positions carried overnight.
    /// `total_unrealized_pnl`: aggregate unrealized P&L of carried positions (GBP).
    pub fn carry_risk(&mut self, carry_count: usize, total_unrealized_pnl: f64, now_ns: u64) -> f64 {
        // Risk factors: count exposure + adverse P&L + bearish Asian sentiment.
        let count_risk = (carry_count as f64 * 10.0).min(40.0);
        let pnl_risk = if total_unrealized_pnl < 0.0 {
            (total_unrealized_pnl.abs() * 0.5).min(30.0)
        } else {
            0.0
        };
        let sentiment_risk = if self.intel.asian_sentiment < -0.3 {
            (self.intel.asian_sentiment.abs() * 30.0).min(30.0)
        } else {
            0.0
        };
        let score = (count_risk + pnl_risk + sentiment_risk).clamp(0.0, 100.0);
        self.intel.carry_risk_score = score;
        self.intel.last_update_ns = now_ns;
        score
    }

    /// Returns true if carry risk exceeds the threshold — caller should reduce exposure.
    pub fn should_reduce_exposure(&self) -> bool {
        self.intel.carry_risk_score > self.risk_threshold
    }

    /// Human-readable summary of current cross-timezone state.
    pub fn sentiment_summary(&self) -> String {
        format!(
            "Asian={:.2} European={:.2} CarryRisk={:.1}/100 Reduce={}",
            self.intel.asian_sentiment,
            self.intel.european_sentiment,
            self.intel.carry_risk_score,
            self.should_reduce_exposure(),
        )
    }

    /// Read-only access to the current intelligence snapshot.
    pub fn intel(&self) -> &TimezoneIntel {
        &self.intel
    }
}

impl Default for CrossTimezoneEngine {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_sentiment_neutral() {
        let engine = CrossTimezoneEngine::new();
        let intel = engine.intel();
        assert!((intel.asian_sentiment - 0.0).abs() < f64::EPSILON);
        assert!((intel.european_sentiment - 0.0).abs() < f64::EPSILON);
        assert!((intel.carry_risk_score - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_bullish_asian_session() {
        let mut engine = CrossTimezoneEngine::new();
        engine.update_asian_close(0.02, 8, 2, 1000);
        assert!(engine.intel().asian_sentiment > 0.0);
    }

    #[test]
    fn test_bearish_asian_session() {
        let mut engine = CrossTimezoneEngine::new();
        engine.update_asian_close(-0.03, 2, 8, 1000);
        assert!(engine.intel().asian_sentiment < 0.0);
    }

    #[test]
    fn test_european_gap_up() {
        let mut engine = CrossTimezoneEngine::new();
        engine.update_european_open(0.015, 2000);
        assert!(engine.intel().european_sentiment > 0.0);
    }

    #[test]
    fn test_carry_risk_high_triggers_reduce() {
        let mut engine = CrossTimezoneEngine::with_params(50.0, 0.8);
        // Bearish Asia + losing carry positions
        engine.update_asian_close(-0.05, 1, 9, 1000);
        let risk = engine.carry_risk(5, -40.0, 2000);
        assert!(risk > 50.0, "Expected risk > 50, got {risk}");
        assert!(engine.should_reduce_exposure());
    }

    #[test]
    fn test_carry_risk_low_no_reduce() {
        let mut engine = CrossTimezoneEngine::new(); // threshold = 70
        let risk = engine.carry_risk(1, 5.0, 1000);
        assert!(risk < 70.0, "Expected risk < 70, got {risk}");
        assert!(!engine.should_reduce_exposure());
    }

    #[test]
    fn test_sentiment_summary_format() {
        let engine = CrossTimezoneEngine::new();
        let summary = engine.sentiment_summary();
        assert!(summary.contains("Asian="));
        assert!(summary.contains("European="));
        assert!(summary.contains("CarryRisk="));
        assert!(summary.contains("Reduce="));
    }

    #[test]
    fn test_sentiment_clamped() {
        let mut engine = CrossTimezoneEngine::with_params(70.0, 0.0); // no decay
        // Extreme return should still clamp sentiment to [-1, 1]
        engine.update_asian_close(0.50, 100, 0, 1000);
        assert!(engine.intel().asian_sentiment <= 1.0);
        assert!(engine.intel().asian_sentiment >= -1.0);

        engine.update_european_open(0.50, 2000);
        assert!(engine.intel().european_sentiment <= 1.0);
        assert!(engine.intel().european_sentiment >= -1.0);
    }
}
