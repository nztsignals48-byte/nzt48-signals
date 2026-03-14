//! Multi-Frame Volatility Analysis (Phase 10)
//!
//! Computes annualized realized volatility across multiple time frames
//! (1m, 5m, 15m, 60m, Daily) using rolling return buffers, then provides
//! a sample-count-weighted consensus estimate.

use std::collections::VecDeque;

/// Maximum number of log-return samples retained per frame.
const MAX_SAMPLES: usize = 200;

/// Minimum samples required before a frame contributes to consensus.
const MIN_SAMPLES_FOR_CONSENSUS: u64 = 30;

// ── Annualization factors: sqrt(trading_periods_per_year) ──────────────
const ANN_1M: f64 = 313.5; // sqrt(252 * 390)
const ANN_5M: f64 = 140.2; // sqrt(252 * 78)
const ANN_15M: f64 = 80.9; // sqrt(252 * 26)
const ANN_60M: f64 = 40.5; // sqrt(252 * 6.5)
const ANN_DAILY: f64 = 15.87; // sqrt(252)

/// Supported bar time frames.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TimeFrame {
    OneMinute,
    FiveMinute,
    FifteenMinute,
    SixtyMinute,
    Daily,
}

impl TimeFrame {
    /// Annualization multiplier for this frame.
    fn annualization_factor(self) -> f64 {
        match self {
            Self::OneMinute => ANN_1M,
            Self::FiveMinute => ANN_5M,
            Self::FifteenMinute => ANN_15M,
            Self::SixtyMinute => ANN_60M,
            Self::Daily => ANN_DAILY,
        }
    }

    /// All variants in order of granularity.
    fn all() -> &'static [TimeFrame] {
        &[
            Self::OneMinute,
            Self::FiveMinute,
            Self::FifteenMinute,
            Self::SixtyMinute,
            Self::Daily,
        ]
    }
}

/// Per-frame volatility estimate.
#[derive(Debug, Clone)]
pub struct FrameVolEstimate {
    pub frame: TimeFrame,
    /// Annualized realized volatility (standard deviation of log returns, scaled).
    pub realized_vol: f64,
    /// Number of return samples used.
    pub sample_count: u64,
    /// Epoch nanoseconds of last recorded return for this frame.
    pub last_update_ns: u64,
}

/// Rolling buffer for a single time frame.
struct FrameBuffer {
    returns: VecDeque<f64>,
    last_update_ns: u64,
}

impl FrameBuffer {
    fn new() -> Self {
        Self {
            returns: VecDeque::with_capacity(MAX_SAMPLES),
            last_update_ns: 0,
        }
    }

    fn push(&mut self, log_return: f64, now_ns: u64) {
        if self.returns.len() == MAX_SAMPLES {
            self.returns.pop_front();
        }
        self.returns.push_back(log_return);
        self.last_update_ns = now_ns;
    }

    fn sample_count(&self) -> u64 {
        self.returns.len() as u64
    }

    /// Compute the sample standard deviation of stored returns.
    /// Returns `None` if fewer than 2 samples.
    fn std_dev(&self) -> Option<f64> {
        let n = self.returns.len();
        if n < 2 {
            return None;
        }
        let nf = n as f64;
        let mean = self.returns.iter().sum::<f64>() / nf;
        let var = self.returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / (nf - 1.0);
        Some(var.sqrt())
    }
}

/// Multi-frame volatility estimator.
///
/// Maintains independent rolling buffers for each [`TimeFrame`] and derives
/// annualized realized-vol estimates plus a sample-weighted consensus.
pub struct MultiFrameVol {
    buf_1m: FrameBuffer,
    buf_5m: FrameBuffer,
    buf_15m: FrameBuffer,
    buf_60m: FrameBuffer,
    buf_daily: FrameBuffer,
}

impl Default for MultiFrameVol {
    fn default() -> Self {
        Self::new()
    }
}

impl MultiFrameVol {
    pub fn new() -> Self {
        Self {
            buf_1m: FrameBuffer::new(),
            buf_5m: FrameBuffer::new(),
            buf_15m: FrameBuffer::new(),
            buf_60m: FrameBuffer::new(),
            buf_daily: FrameBuffer::new(),
        }
    }

    fn buffer_mut(&mut self, frame: TimeFrame) -> &mut FrameBuffer {
        match frame {
            TimeFrame::OneMinute => &mut self.buf_1m,
            TimeFrame::FiveMinute => &mut self.buf_5m,
            TimeFrame::FifteenMinute => &mut self.buf_15m,
            TimeFrame::SixtyMinute => &mut self.buf_60m,
            TimeFrame::Daily => &mut self.buf_daily,
        }
    }

    fn buffer(&self, frame: TimeFrame) -> &FrameBuffer {
        match frame {
            TimeFrame::OneMinute => &self.buf_1m,
            TimeFrame::FiveMinute => &self.buf_5m,
            TimeFrame::FifteenMinute => &self.buf_15m,
            TimeFrame::SixtyMinute => &self.buf_60m,
            TimeFrame::Daily => &self.buf_daily,
        }
    }

    /// Record a log return for the given time frame.
    pub fn record_return(&mut self, frame: TimeFrame, log_return: f64, now_ns: u64) {
        self.buffer_mut(frame).push(log_return, now_ns);
    }

    /// Compute the annualized realized-vol estimate for a single frame.
    /// Returns `None` if fewer than 2 samples are available.
    pub fn estimate(&self, frame: TimeFrame) -> Option<FrameVolEstimate> {
        let buf = self.buffer(frame);
        let sd = buf.std_dev()?;
        Some(FrameVolEstimate {
            frame,
            realized_vol: sd * frame.annualization_factor(),
            sample_count: buf.sample_count(),
            last_update_ns: buf.last_update_ns,
        })
    }

    /// Return the frame with the most recorded samples (most reliable estimate).
    pub fn best_frame(&self) -> Option<TimeFrame> {
        TimeFrame::all()
            .iter()
            .filter(|f| self.buffer(**f).sample_count() >= 2)
            .max_by_key(|f| self.buffer(**f).sample_count())
            .copied()
    }

    /// Sample-count-weighted average of annualized vol across all frames
    /// that have at least [`MIN_SAMPLES_FOR_CONSENSUS`] samples.
    ///
    /// Returns `0.0` if no frame qualifies.
    pub fn consensus_vol(&self) -> f64 {
        let mut weight_sum: f64 = 0.0;
        let mut vol_weighted_sum: f64 = 0.0;

        for &frame in TimeFrame::all() {
            if let Some(est) = self.estimate(frame) {
                if est.sample_count < MIN_SAMPLES_FOR_CONSENSUS {
                    continue;
                }
                let w = est.sample_count as f64;
                vol_weighted_sum += w * est.realized_vol;
                weight_sum += w;
            }
        }

        if weight_sum > 0.0 {
            vol_weighted_sum / weight_sum
        } else {
            0.0
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_estimate_returns_none() {
        let mfv = MultiFrameVol::new();
        assert!(mfv.estimate(TimeFrame::Daily).is_none());
        assert!(mfv.best_frame().is_none());
        assert!((mfv.consensus_vol() - 0.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_single_sample_returns_none() {
        let mut mfv = MultiFrameVol::new();
        mfv.record_return(TimeFrame::Daily, 0.01, 1_000);
        assert!(mfv.estimate(TimeFrame::Daily).is_none());
    }

    #[test]
    fn test_two_samples_produces_estimate() {
        let mut mfv = MultiFrameVol::new();
        mfv.record_return(TimeFrame::Daily, 0.01, 1_000);
        mfv.record_return(TimeFrame::Daily, -0.01, 2_000);
        let est = mfv.estimate(TimeFrame::Daily);
        assert!(est.is_some());
        let est = est.expect("just checked");
        assert!(est.realized_vol > 0.0);
        assert_eq!(est.sample_count, 2);
        assert_eq!(est.last_update_ns, 2_000);
    }

    #[test]
    fn test_annualization_scales_correctly() {
        // With constant returns the std dev is 0, so use alternating returns.
        let mut mfv = MultiFrameVol::new();
        for i in 0..50 {
            let r = if i % 2 == 0 { 0.001 } else { -0.001 };
            mfv.record_return(TimeFrame::OneMinute, r, i);
            mfv.record_return(TimeFrame::Daily, r, i);
        }
        let vol_1m = mfv.estimate(TimeFrame::OneMinute).expect("has samples").realized_vol;
        let vol_d = mfv.estimate(TimeFrame::Daily).expect("has samples").realized_vol;
        // 1-min annualization factor (313.5) is ~20x Daily (15.87).
        // Same raw std-dev, so vol_1m / vol_d ≈ 313.5/15.87 ≈ 19.75
        let ratio = vol_1m / vol_d;
        assert!((ratio - (ANN_1M / ANN_DAILY)).abs() < 0.1);
    }

    #[test]
    fn test_best_frame_picks_most_samples() {
        let mut mfv = MultiFrameVol::new();
        for i in 0..10u64 {
            let r = if i % 2 == 0 { 0.001 } else { -0.001 };
            mfv.record_return(TimeFrame::FiveMinute, r, i);
        }
        for i in 0..20u64 {
            let r = if i % 2 == 0 { 0.001 } else { -0.001 };
            mfv.record_return(TimeFrame::FifteenMinute, r, i);
        }
        assert_eq!(mfv.best_frame(), Some(TimeFrame::FifteenMinute));
    }

    #[test]
    fn test_consensus_requires_min_samples() {
        let mut mfv = MultiFrameVol::new();
        // 10 samples — below MIN_SAMPLES_FOR_CONSENSUS (30)
        for i in 0..10u64 {
            mfv.record_return(TimeFrame::Daily, if i % 2 == 0 { 0.01 } else { -0.01 }, i);
        }
        assert!((mfv.consensus_vol() - 0.0).abs() < f64::EPSILON);

        // Push to 35 samples — now qualifies
        for i in 10..35u64 {
            mfv.record_return(TimeFrame::Daily, if i % 2 == 0 { 0.01 } else { -0.01 }, i);
        }
        assert!(mfv.consensus_vol() > 0.0);
    }

    #[test]
    fn test_buffer_bounded_at_max_samples() {
        let mut mfv = MultiFrameVol::new();
        for i in 0..250u64 {
            mfv.record_return(TimeFrame::OneMinute, 0.001 * (i as f64), i);
        }
        let est = mfv.estimate(TimeFrame::OneMinute).expect("has samples");
        assert_eq!(est.sample_count, MAX_SAMPLES as u64);
    }

    #[test]
    fn test_consensus_weighted_average() {
        let mut mfv = MultiFrameVol::new();
        // Feed two frames with different sample counts and different vol profiles
        // Frame 1: 50 samples of small returns
        for i in 0..50u64 {
            let r = if i % 2 == 0 { 0.001 } else { -0.001 };
            mfv.record_return(TimeFrame::FiveMinute, r, i);
        }
        // Frame 2: 40 samples of larger returns
        for i in 0..40u64 {
            let r = if i % 2 == 0 { 0.005 } else { -0.005 };
            mfv.record_return(TimeFrame::FifteenMinute, r, i);
        }
        let vol_5m = mfv.estimate(TimeFrame::FiveMinute).expect("ok").realized_vol;
        let vol_15m = mfv.estimate(TimeFrame::FifteenMinute).expect("ok").realized_vol;
        let consensus = mfv.consensus_vol();
        // Weighted average: (50*vol_5m + 40*vol_15m) / 90
        let expected = (50.0 * vol_5m + 40.0 * vol_15m) / 90.0;
        assert!((consensus - expected).abs() < 1e-10);
    }
}
