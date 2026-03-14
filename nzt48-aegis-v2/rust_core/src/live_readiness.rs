//! P25: Live Capital Readiness Gate (Post-Crucible).
//! 63-day gauntlet: 100+ validated trades, WR ≥ 40%, Sharpe > 0, DD < 8%.
//! Human review required. IS_LIVE transition gate.

/// Crucible validation metrics.
#[derive(Clone, Debug)]
pub struct CrucibleMetrics {
    /// Total completed paper trades.
    pub trade_count: u32,
    /// Win rate (0.0 - 1.0).
    pub win_rate: f64,
    /// Annualized Sharpe ratio.
    pub sharpe_ratio: f64,
    /// Maximum drawdown as fraction (0.0 - 1.0).
    pub max_drawdown: f64,
    /// Profit factor (gross profit / gross loss).
    pub profit_factor: f64,
    /// Days of paper trading completed.
    pub days_elapsed: u32,
    /// Whether all 16 Runtime Invariants passed 100%.
    pub invariants_verified: bool,
    /// Whether human review has been completed.
    pub human_reviewed: bool,
}

impl Default for CrucibleMetrics {
    fn default() -> Self {
        Self {
            trade_count: 0,
            win_rate: 0.0,
            sharpe_ratio: 0.0,
            max_drawdown: 0.0,
            profit_factor: 0.0,
            days_elapsed: 0,
            invariants_verified: false,
            human_reviewed: false,
        }
    }
}

/// Result of the live readiness check.
#[derive(Clone, Debug)]
pub struct ReadinessResult {
    pub is_ready: bool,
    pub failing_criteria: Vec<String>,
    pub passing_criteria: Vec<String>,
}

/// The live readiness gate — checks all criteria for IS_LIVE transition.
pub struct LiveReadinessGate {
    /// Minimum trades required.
    min_trades: u32,
    /// Minimum win rate (0.0 - 1.0).
    min_win_rate: f64,
    /// Minimum Sharpe ratio.
    min_sharpe: f64,
    /// Maximum allowed drawdown (fraction).
    max_drawdown: f64,
    /// Minimum profit factor.
    min_profit_factor: f64,
    /// Minimum days of paper trading.
    min_days: u32,
}

impl LiveReadinessGate {
    pub fn new() -> Self {
        Self {
            min_trades: 100,
            min_win_rate: 0.40,
            min_sharpe: 0.0,
            max_drawdown: 0.08,
            min_profit_factor: 1.0,
            min_days: 63,
        }
    }

    /// Evaluate all readiness criteria.
    pub fn evaluate(&self, metrics: &CrucibleMetrics) -> ReadinessResult {
        let mut failing = Vec::new();
        let mut passing = Vec::new();

        // Trade count
        if metrics.trade_count >= self.min_trades {
            passing.push(format!(
                "Trade count: {} >= {}",
                metrics.trade_count, self.min_trades
            ));
        } else {
            failing.push(format!(
                "Trade count: {} < {} required",
                metrics.trade_count, self.min_trades
            ));
        }

        // Win rate
        if metrics.win_rate >= self.min_win_rate {
            passing.push(format!(
                "Win rate: {:.1}% >= {:.1}%",
                metrics.win_rate * 100.0,
                self.min_win_rate * 100.0
            ));
        } else {
            failing.push(format!(
                "Win rate: {:.1}% < {:.1}% required",
                metrics.win_rate * 100.0,
                self.min_win_rate * 100.0
            ));
        }

        // Sharpe ratio
        if metrics.sharpe_ratio > self.min_sharpe {
            passing.push(format!(
                "Sharpe: {:.3} > {:.1}",
                metrics.sharpe_ratio, self.min_sharpe
            ));
        } else {
            failing.push(format!(
                "Sharpe: {:.3} <= {:.1} required",
                metrics.sharpe_ratio, self.min_sharpe
            ));
        }

        // Max drawdown
        if metrics.max_drawdown < self.max_drawdown {
            passing.push(format!(
                "Max DD: {:.1}% < {:.1}%",
                metrics.max_drawdown * 100.0,
                self.max_drawdown * 100.0
            ));
        } else {
            failing.push(format!(
                "Max DD: {:.1}% >= {:.1}% limit",
                metrics.max_drawdown * 100.0,
                self.max_drawdown * 100.0
            ));
        }

        // Profit factor
        if metrics.profit_factor > self.min_profit_factor {
            passing.push(format!(
                "Profit factor: {:.2} > {:.1}",
                metrics.profit_factor, self.min_profit_factor
            ));
        } else {
            failing.push(format!(
                "Profit factor: {:.2} <= {:.1} required",
                metrics.profit_factor, self.min_profit_factor
            ));
        }

        // Days elapsed
        if metrics.days_elapsed >= self.min_days {
            passing.push(format!(
                "Days: {} >= {}",
                metrics.days_elapsed, self.min_days
            ));
        } else {
            failing.push(format!(
                "Days: {} < {} required",
                metrics.days_elapsed, self.min_days
            ));
        }

        // Invariants
        if metrics.invariants_verified {
            passing.push("Invariants: VERIFIED".to_string());
        } else {
            failing.push("Invariants: NOT VERIFIED".to_string());
        }

        // Human review
        if metrics.human_reviewed {
            passing.push("Human review: APPROVED".to_string());
        } else {
            failing.push("Human review: PENDING".to_string());
        }

        ReadinessResult {
            is_ready: failing.is_empty(),
            failing_criteria: failing,
            passing_criteria: passing,
        }
    }
}

impl Default for LiveReadinessGate {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_all_criteria_pass() {
        let gate = LiveReadinessGate::new();
        let metrics = CrucibleMetrics {
            trade_count: 120,
            win_rate: 0.45,
            sharpe_ratio: 0.5,
            max_drawdown: 0.05,
            profit_factor: 1.3,
            days_elapsed: 70,
            invariants_verified: true,
            human_reviewed: true,
        };
        let result = gate.evaluate(&metrics);
        assert!(result.is_ready, "All criteria met: {:?}", result.failing_criteria);
        assert!(result.failing_criteria.is_empty());
    }

    #[test]
    fn test_insufficient_trades() {
        let gate = LiveReadinessGate::new();
        let metrics = CrucibleMetrics {
            trade_count: 50,
            win_rate: 0.50,
            sharpe_ratio: 1.0,
            max_drawdown: 0.03,
            profit_factor: 2.0,
            days_elapsed: 70,
            invariants_verified: true,
            human_reviewed: true,
        };
        let result = gate.evaluate(&metrics);
        assert!(!result.is_ready);
        assert!(result.failing_criteria.iter().any(|c| c.contains("Trade count")));
    }

    #[test]
    fn test_low_win_rate() {
        let gate = LiveReadinessGate::new();
        let metrics = CrucibleMetrics {
            trade_count: 150,
            win_rate: 0.30,
            sharpe_ratio: 0.5,
            max_drawdown: 0.05,
            profit_factor: 1.5,
            days_elapsed: 70,
            invariants_verified: true,
            human_reviewed: true,
        };
        let result = gate.evaluate(&metrics);
        assert!(!result.is_ready);
        assert!(result.failing_criteria.iter().any(|c| c.contains("Win rate")));
    }

    #[test]
    fn test_high_drawdown() {
        let gate = LiveReadinessGate::new();
        let metrics = CrucibleMetrics {
            trade_count: 150,
            win_rate: 0.50,
            sharpe_ratio: 0.5,
            max_drawdown: 0.10,
            profit_factor: 1.5,
            days_elapsed: 70,
            invariants_verified: true,
            human_reviewed: true,
        };
        let result = gate.evaluate(&metrics);
        assert!(!result.is_ready);
        assert!(result.failing_criteria.iter().any(|c| c.contains("Max DD")));
    }

    #[test]
    fn test_no_human_review() {
        let gate = LiveReadinessGate::new();
        let metrics = CrucibleMetrics {
            trade_count: 150,
            win_rate: 0.50,
            sharpe_ratio: 0.5,
            max_drawdown: 0.03,
            profit_factor: 1.5,
            days_elapsed: 70,
            invariants_verified: true,
            human_reviewed: false,
        };
        let result = gate.evaluate(&metrics);
        assert!(!result.is_ready);
        assert!(result.failing_criteria.iter().any(|c| c.contains("Human review")));
    }

    #[test]
    fn test_default_metrics_fail() {
        let gate = LiveReadinessGate::new();
        let metrics = CrucibleMetrics::default();
        let result = gate.evaluate(&metrics);
        assert!(!result.is_ready);
        assert!(result.failing_criteria.len() >= 6);
    }
}
