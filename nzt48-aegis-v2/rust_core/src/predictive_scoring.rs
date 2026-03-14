//! P12: Predictive Scoring — Alpha Sieve enhancement with IC breakdown.
//! Decision tree on Information Coefficient by ticker, time-of-day, regime, macro state.
//! Auto-locks underperforming tickers after consecutive negative trades.

use crate::types::TickerId;
use std::collections::HashMap;

/// Per-ticker scoring state for predictive alpha assessment.
#[derive(Clone, Debug)]
pub struct TickerScore {
    /// Rolling IC (Information Coefficient) — simplified as win fraction - 0.5.
    pub ic: f64,
    /// Number of trades observed for this ticker.
    pub trade_count: u32,
    /// Consecutive negative trades (for auto-lock).
    pub consecutive_losses: u32,
    /// Whether this ticker is locked from Vanguard.
    pub locked: bool,
    /// IC by time-of-day bucket (morning/midday/afternoon).
    pub ic_by_tod: [f64; 3],
    /// Trade count by time-of-day bucket.
    pub trades_by_tod: [u32; 3],
}

impl TickerScore {
    fn new() -> Self {
        Self {
            ic: 0.0,
            trade_count: 0,
            consecutive_losses: 0,
            locked: false,
            ic_by_tod: [0.0; 3],
            trades_by_tod: [0; 3],
        }
    }
}

/// Time-of-day bucket for IC breakdown.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeBucket {
    /// 08:00-10:30 London.
    Morning,
    /// 10:30-14:00 London.
    Midday,
    /// 14:00-16:30 London.
    Afternoon,
}

impl TimeBucket {
    /// Map London seconds-from-midnight to a bucket.
    pub fn from_london_secs(secs: u32) -> Self {
        if secs < 10 * 3600 + 30 * 60 {
            TimeBucket::Morning
        } else if secs < 14 * 3600 {
            TimeBucket::Midday
        } else {
            TimeBucket::Afternoon
        }
    }

    fn index(self) -> usize {
        match self {
            TimeBucket::Morning => 0,
            TimeBucket::Midday => 1,
            TimeBucket::Afternoon => 2,
        }
    }
}

/// Predictive scoring engine — tracks IC and auto-locks underperformers.
pub struct PredictiveScorer {
    scores: HashMap<TickerId, TickerScore>,
    /// Number of consecutive losses before auto-lock.
    lock_threshold: u32,
    /// IC threshold below which a ticker is flagged.
    ic_warning_threshold: f64,
}

impl PredictiveScorer {
    pub fn new() -> Self {
        Self {
            scores: HashMap::new(),
            lock_threshold: 5,
            ic_warning_threshold: 0.02,
        }
    }

    /// Record a trade outcome for a ticker.
    pub fn record_trade(
        &mut self,
        ticker_id: TickerId,
        pnl: f64,
        london_time_secs: u32,
    ) {
        let score = self.scores.entry(ticker_id).or_insert_with(TickerScore::new);
        let is_win = pnl > 0.0;

        score.trade_count += 1;

        // Update consecutive losses.
        if is_win {
            score.consecutive_losses = 0;
        } else {
            score.consecutive_losses += 1;
        }

        // Auto-lock after threshold consecutive losses.
        if score.consecutive_losses >= self.lock_threshold {
            score.locked = true;
        }

        // Update rolling IC: simple fraction of wins - 0.5 (expanding window).
        let win_frac = score.ic + 0.5; // Convert back from IC to fraction
        let wins_so_far = (win_frac * (score.trade_count - 1) as f64)
            + if is_win { 1.0 } else { 0.0 };
        score.ic = (wins_so_far / score.trade_count as f64) - 0.5;

        // Update time-of-day IC.
        let bucket = TimeBucket::from_london_secs(london_time_secs);
        let idx = bucket.index();
        score.trades_by_tod[idx] += 1;
        let frac = score.ic_by_tod[idx] + 0.5;
        let tod_wins = (frac * (score.trades_by_tod[idx] - 1) as f64)
            + if is_win { 1.0 } else { 0.0 };
        score.ic_by_tod[idx] = (tod_wins / score.trades_by_tod[idx] as f64) - 0.5;
    }

    /// Check if a ticker is locked from Vanguard.
    pub fn is_locked(&self, ticker_id: TickerId) -> bool {
        self.scores.get(&ticker_id).is_some_and(|s| s.locked)
    }

    /// Unlock a ticker (manual override or after recalibration).
    pub fn unlock(&mut self, ticker_id: TickerId) {
        if let Some(score) = self.scores.get_mut(&ticker_id) {
            score.locked = false;
            score.consecutive_losses = 0;
        }
    }

    /// Get the IC for a ticker, optionally filtered by time-of-day.
    pub fn ic(&self, ticker_id: TickerId, bucket: Option<TimeBucket>) -> f64 {
        let score = match self.scores.get(&ticker_id) {
            Some(s) => s,
            None => return 0.0,
        };
        match bucket {
            Some(b) => score.ic_by_tod[b.index()],
            None => score.ic,
        }
    }

    /// Get the ticker score for inspection.
    pub fn score(&self, ticker_id: TickerId) -> Option<&TickerScore> {
        self.scores.get(&ticker_id)
    }

    /// Return all tickers with IC below warning threshold.
    pub fn flagged_tickers(&self) -> Vec<TickerId> {
        self.scores
            .iter()
            .filter(|(_, s)| s.ic < self.ic_warning_threshold && s.trade_count >= 5)
            .map(|(&tid, _)| tid)
            .collect()
    }

    /// Best time-of-day bucket for a ticker (highest IC).
    pub fn best_time_bucket(&self, ticker_id: TickerId) -> Option<TimeBucket> {
        let score = self.scores.get(&ticker_id)?;
        let buckets = [TimeBucket::Morning, TimeBucket::Midday, TimeBucket::Afternoon];
        buckets
            .iter()
            .filter(|b| score.trades_by_tod[b.index()] >= 3)
            .max_by(|a, b| {
                score.ic_by_tod[a.index()]
                    .partial_cmp(&score.ic_by_tod[b.index()])
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .copied()
    }
}

impl Default for PredictiveScorer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_consecutive_losses_lock() {
        let mut scorer = PredictiveScorer::new();
        let tid = TickerId(1);
        for _ in 0..5 {
            scorer.record_trade(tid, -10.0, 10 * 3600);
        }
        assert!(scorer.is_locked(tid));
    }

    #[test]
    fn test_win_resets_consecutive_losses() {
        let mut scorer = PredictiveScorer::new();
        let tid = TickerId(1);
        scorer.record_trade(tid, -10.0, 10 * 3600);
        scorer.record_trade(tid, -10.0, 10 * 3600);
        scorer.record_trade(tid, 10.0, 10 * 3600);
        let s = scorer.score(tid).expect("score exists");
        assert_eq!(s.consecutive_losses, 0);
        assert!(!s.locked);
    }

    #[test]
    fn test_ic_positive_for_winners() {
        let mut scorer = PredictiveScorer::new();
        let tid = TickerId(1);
        for _ in 0..10 {
            scorer.record_trade(tid, 10.0, 10 * 3600);
        }
        assert!(scorer.ic(tid, None) > 0.0);
    }

    #[test]
    fn test_ic_negative_for_losers() {
        let mut scorer = PredictiveScorer::new();
        let tid = TickerId(1);
        for _ in 0..10 {
            scorer.record_trade(tid, -10.0, 10 * 3600);
        }
        assert!(scorer.ic(tid, None) < 0.0);
    }

    #[test]
    fn test_unlock_clears_lock() {
        let mut scorer = PredictiveScorer::new();
        let tid = TickerId(1);
        for _ in 0..5 {
            scorer.record_trade(tid, -10.0, 10 * 3600);
        }
        assert!(scorer.is_locked(tid));
        scorer.unlock(tid);
        assert!(!scorer.is_locked(tid));
    }

    #[test]
    fn test_flagged_tickers() {
        let mut scorer = PredictiveScorer::new();
        let tid = TickerId(1);
        for _ in 0..10 {
            scorer.record_trade(tid, -5.0, 10 * 3600);
        }
        let flagged = scorer.flagged_tickers();
        assert!(flagged.contains(&tid));
    }

    #[test]
    fn test_time_bucket_mapping() {
        assert_eq!(TimeBucket::from_london_secs(9 * 3600), TimeBucket::Morning);
        assert_eq!(TimeBucket::from_london_secs(12 * 3600), TimeBucket::Midday);
        assert_eq!(TimeBucket::from_london_secs(15 * 3600), TimeBucket::Afternoon);
    }

    #[test]
    fn test_tod_ic_tracking() {
        let mut scorer = PredictiveScorer::new();
        let tid = TickerId(1);
        // All wins in morning
        for _ in 0..5 {
            scorer.record_trade(tid, 10.0, 9 * 3600);
        }
        // All losses in afternoon
        for _ in 0..5 {
            scorer.record_trade(tid, -10.0, 15 * 3600);
        }
        assert!(scorer.ic(tid, Some(TimeBucket::Morning)) > 0.0);
        assert!(scorer.ic(tid, Some(TimeBucket::Afternoon)) < 0.0);
    }
}
