//! P22: Latency Profiling & Optimization.
//! Profiles tick-to-trade (T2T) latency across the pipeline.
//! Target: <500ms T2T, <100ms order submission.

use std::collections::VecDeque;

/// A single latency measurement.
#[derive(Clone, Copy, Debug)]
pub struct LatencyMeasurement {
    /// Start timestamp in nanoseconds.
    pub start_ns: u64,
    /// End timestamp in nanoseconds.
    pub end_ns: u64,
}

impl LatencyMeasurement {
    pub fn duration_us(&self) -> f64 {
        (self.end_ns.saturating_sub(self.start_ns)) as f64 / 1_000.0
    }

    pub fn duration_ms(&self) -> f64 {
        (self.end_ns.saturating_sub(self.start_ns)) as f64 / 1_000_000.0
    }
}

/// Pipeline stage for latency tracking.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum PipelineStage {
    /// Tick received → signal generated.
    TickToSignal,
    /// Signal generated → risk check complete.
    SignalToRiskCheck,
    /// Risk check → order submitted.
    RiskToOrder,
    /// Order submitted → fill confirmed.
    OrderToFill,
    /// Full tick-to-trade (T2T).
    TickToTrade,
    /// Python bridge round-trip.
    PythonBridge,
}

/// Per-stage latency statistics.
#[derive(Clone, Debug)]
pub struct StageStats {
    pub stage: PipelineStage,
    pub count: u64,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub p99_ms: f64,
    pub mean_ms: f64,
    pub max_ms: f64,
}

/// Latency profiler — tracks per-stage latency distributions.
pub struct LatencyProfiler {
    /// Per-stage measurement buffers (bounded to max_samples).
    buffers: [VecDeque<f64>; 6],
    max_samples: usize,
    /// T2T target in milliseconds.
    t2t_target_ms: f64,
    /// Order submission target in milliseconds.
    order_target_ms: f64,
}

impl LatencyProfiler {
    pub fn new() -> Self {
        Self {
            buffers: [
                VecDeque::with_capacity(1000),
                VecDeque::with_capacity(1000),
                VecDeque::with_capacity(1000),
                VecDeque::with_capacity(1000),
                VecDeque::with_capacity(1000),
                VecDeque::with_capacity(1000),
            ],
            max_samples: 1000,
            t2t_target_ms: 500.0,
            order_target_ms: 100.0,
        }
    }

    fn stage_index(stage: PipelineStage) -> usize {
        match stage {
            PipelineStage::TickToSignal => 0,
            PipelineStage::SignalToRiskCheck => 1,
            PipelineStage::RiskToOrder => 2,
            PipelineStage::OrderToFill => 3,
            PipelineStage::TickToTrade => 4,
            PipelineStage::PythonBridge => 5,
        }
    }

    /// Record a latency measurement for a pipeline stage.
    pub fn record(&mut self, stage: PipelineStage, measurement: LatencyMeasurement) {
        let idx = Self::stage_index(stage);
        let buf = &mut self.buffers[idx];
        buf.push_back(measurement.duration_ms());
        if buf.len() > self.max_samples {
            buf.pop_front();
        }
    }

    /// Record a raw duration in milliseconds.
    pub fn record_ms(&mut self, stage: PipelineStage, duration_ms: f64) {
        let idx = Self::stage_index(stage);
        let buf = &mut self.buffers[idx];
        buf.push_back(duration_ms);
        if buf.len() > self.max_samples {
            buf.pop_front();
        }
    }

    /// Get statistics for a pipeline stage.
    pub fn stats(&self, stage: PipelineStage) -> StageStats {
        let idx = Self::stage_index(stage);
        let buf = &self.buffers[idx];

        if buf.is_empty() {
            return StageStats {
                stage,
                count: 0,
                p50_ms: 0.0,
                p95_ms: 0.0,
                p99_ms: 0.0,
                mean_ms: 0.0,
                max_ms: 0.0,
            };
        }

        let mut sorted: Vec<f64> = buf.iter().copied().collect();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let n = sorted.len();

        let mean = sorted.iter().sum::<f64>() / n as f64;
        let p50 = sorted[n / 2];
        let p95 = sorted[(n as f64 * 0.95) as usize];
        let p99 = sorted[((n as f64 * 0.99) as usize).min(n - 1)];
        let max = sorted[n - 1];

        StageStats {
            stage,
            count: n as u64,
            p50_ms: p50,
            p95_ms: p95,
            p99_ms: p99,
            mean_ms: mean,
            max_ms: max,
        }
    }

    /// Check if T2T latency exceeds target.
    pub fn t2t_exceeds_target(&self) -> bool {
        let stats = self.stats(PipelineStage::TickToTrade);
        stats.count > 0 && stats.p50_ms > self.t2t_target_ms
    }

    /// Check if order submission latency exceeds target.
    pub fn order_exceeds_target(&self) -> bool {
        let stats = self.stats(PipelineStage::RiskToOrder);
        stats.count > 0 && stats.p50_ms > self.order_target_ms
    }

    /// Identify the bottleneck stage (highest p50 latency).
    pub fn bottleneck(&self) -> Option<PipelineStage> {
        let stages = [
            PipelineStage::TickToSignal,
            PipelineStage::SignalToRiskCheck,
            PipelineStage::RiskToOrder,
            PipelineStage::OrderToFill,
            PipelineStage::PythonBridge,
        ];

        stages
            .iter()
            .filter(|s| self.stats(**s).count > 0)
            .max_by(|a, b| {
                let sa = self.stats(**a);
                let sb = self.stats(**b);
                sa.p50_ms
                    .partial_cmp(&sb.p50_ms)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .copied()
    }

    /// Get a summary of all stages.
    pub fn summary(&self) -> Vec<StageStats> {
        let stages = [
            PipelineStage::TickToSignal,
            PipelineStage::SignalToRiskCheck,
            PipelineStage::RiskToOrder,
            PipelineStage::OrderToFill,
            PipelineStage::TickToTrade,
            PipelineStage::PythonBridge,
        ];
        stages.iter().map(|s| self.stats(*s)).collect()
    }
}

impl Default for LatencyProfiler {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_measurement_duration() {
        let m = LatencyMeasurement {
            start_ns: 1_000_000,
            end_ns: 2_000_000,
        };
        assert!((m.duration_ms() - 1.0).abs() < 0.001);
        assert!((m.duration_us() - 1000.0).abs() < 0.1);
    }

    #[test]
    fn test_record_and_stats() {
        let mut profiler = LatencyProfiler::new();
        for i in 0..100 {
            profiler.record_ms(PipelineStage::TickToSignal, i as f64);
        }
        let stats = profiler.stats(PipelineStage::TickToSignal);
        assert_eq!(stats.count, 100);
        assert!(stats.p50_ms >= 45.0 && stats.p50_ms <= 55.0);
        assert!(stats.p99_ms >= 95.0);
        assert!((stats.max_ms - 99.0).abs() < 0.1);
    }

    #[test]
    fn test_empty_stats() {
        let profiler = LatencyProfiler::new();
        let stats = profiler.stats(PipelineStage::TickToTrade);
        assert_eq!(stats.count, 0);
        assert_eq!(stats.p50_ms, 0.0);
    }

    #[test]
    fn test_t2t_target_check() {
        let mut profiler = LatencyProfiler::new();
        // All fast
        for _ in 0..50 {
            profiler.record_ms(PipelineStage::TickToTrade, 100.0);
        }
        assert!(!profiler.t2t_exceeds_target());

        // All slow
        for _ in 0..100 {
            profiler.record_ms(PipelineStage::TickToTrade, 600.0);
        }
        assert!(profiler.t2t_exceeds_target());
    }

    #[test]
    fn test_bottleneck_detection() {
        let mut profiler = LatencyProfiler::new();
        profiler.record_ms(PipelineStage::TickToSignal, 10.0);
        profiler.record_ms(PipelineStage::PythonBridge, 200.0);
        profiler.record_ms(PipelineStage::RiskToOrder, 5.0);

        let bottleneck = profiler.bottleneck();
        assert_eq!(bottleneck, Some(PipelineStage::PythonBridge));
    }

    #[test]
    fn test_buffer_bounded() {
        let mut profiler = LatencyProfiler::new();
        for i in 0..2000 {
            profiler.record_ms(PipelineStage::TickToSignal, i as f64);
        }
        let stats = profiler.stats(PipelineStage::TickToSignal);
        assert_eq!(stats.count, 1000); // max_samples
    }

    #[test]
    fn test_summary() {
        let profiler = LatencyProfiler::new();
        let summary = profiler.summary();
        assert_eq!(summary.len(), 6); // All 6 stages
    }
}
