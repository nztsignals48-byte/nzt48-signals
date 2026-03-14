//! Phase 17: Telemetry stack — structured metrics, latency tracking, health monitoring.
//! Lightweight counters + ring buffers. No external dependencies (no Prometheus/StatsD).
//! All metrics are lock-free for hot-path safety.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

/// Atomic counter for a single metric.
pub struct Counter {
    value: AtomicU64,
}

impl Counter {
    pub fn new() -> Self {
        Self {
            value: AtomicU64::new(0),
        }
    }

    pub fn inc(&self) {
        self.value.fetch_add(1, Ordering::Relaxed);
    }

    pub fn add(&self, n: u64) {
        self.value.fetch_add(n, Ordering::Relaxed);
    }

    pub fn get(&self) -> u64 {
        self.value.load(Ordering::Relaxed)
    }

    pub fn reset(&self) {
        self.value.store(0, Ordering::Relaxed);
    }
}

impl Default for Counter {
    fn default() -> Self {
        Self::new()
    }
}

/// Fixed-size ring buffer for latency samples (nanoseconds).
/// Lock-free via index wrapping.
pub struct LatencyRing {
    samples: Vec<AtomicU64>,
    head: AtomicU64,
    capacity: usize,
}

impl LatencyRing {
    pub fn new(capacity: usize) -> Self {
        let mut samples = Vec::with_capacity(capacity);
        for _ in 0..capacity {
            samples.push(AtomicU64::new(0));
        }
        Self {
            samples,
            head: AtomicU64::new(0),
            capacity,
        }
    }

    /// Record a latency sample.
    pub fn record(&self, latency_ns: u64) {
        let idx = self.head.fetch_add(1, Ordering::Relaxed) as usize % self.capacity;
        self.samples[idx].store(latency_ns, Ordering::Relaxed);
    }

    /// Compute P50/P95/P99 latency (approximate, non-atomic snapshot).
    pub fn percentiles(&self) -> LatencyPercentiles {
        let mut vals: Vec<u64> = self
            .samples
            .iter()
            .map(|s| s.load(Ordering::Relaxed))
            .filter(|&v| v > 0)
            .collect();

        if vals.is_empty() {
            return LatencyPercentiles {
                p50_ns: 0,
                p95_ns: 0,
                p99_ns: 0,
                count: 0,
            };
        }

        vals.sort_unstable();
        let n = vals.len();
        LatencyPercentiles {
            p50_ns: vals[n / 2],
            p95_ns: vals[(n as f64 * 0.95) as usize],
            p99_ns: vals[((n as f64 * 0.99) as usize).min(n - 1)],
            count: n as u64,
        }
    }
}

/// Latency percentile snapshot.
#[derive(Clone, Debug)]
pub struct LatencyPercentiles {
    pub p50_ns: u64,
    pub p95_ns: u64,
    pub p99_ns: u64,
    pub count: u64,
}

impl LatencyPercentiles {
    pub fn p50_ms(&self) -> f64 {
        self.p50_ns as f64 / 1_000_000.0
    }
    pub fn p95_ms(&self) -> f64 {
        self.p95_ns as f64 / 1_000_000.0
    }
    pub fn p99_ms(&self) -> f64 {
        self.p99_ns as f64 / 1_000_000.0
    }
}

/// Engine-wide telemetry metrics.
pub struct Telemetry {
    // ── Tick pipeline ──
    pub ticks_received: Counter,
    pub ticks_filtered: Counter,
    pub ticks_routed_vanguard: Counter,
    pub ticks_routed_apex: Counter,
    pub ticks_dropped: Counter,

    // ── Signal pipeline ──
    pub signals_generated: Counter,
    pub signals_approved: Counter,
    pub signals_vetoed: Counter,

    // ── Order lifecycle ──
    pub orders_submitted: Counter,
    pub orders_filled: Counter,
    pub orders_cancelled: Counter,
    pub orders_rejected: Counter,

    // ── Exit engine ──
    pub exits_chandelier: Counter,
    pub exits_eod: Counter,
    pub exits_halt: Counter,
    pub exits_dust: Counter,

    // ── Risk regime ──
    pub regime_escalations: Counter,
    pub reconciliation_runs: Counter,
    pub reconciliation_mismatches: Counter,

    // ── Latency ──
    pub tick_to_trade_latency: LatencyRing,
    pub brain_signal_latency: LatencyRing,
    pub broker_ack_latency: LatencyRing,

    // ── Per-veto-reason counters ──
    pub veto_counts: HashMap<String, Counter>,
}

impl Telemetry {
    pub fn new() -> Self {
        Self {
            ticks_received: Counter::new(),
            ticks_filtered: Counter::new(),
            ticks_routed_vanguard: Counter::new(),
            ticks_routed_apex: Counter::new(),
            ticks_dropped: Counter::new(),

            signals_generated: Counter::new(),
            signals_approved: Counter::new(),
            signals_vetoed: Counter::new(),

            orders_submitted: Counter::new(),
            orders_filled: Counter::new(),
            orders_cancelled: Counter::new(),
            orders_rejected: Counter::new(),

            exits_chandelier: Counter::new(),
            exits_eod: Counter::new(),
            exits_halt: Counter::new(),
            exits_dust: Counter::new(),

            regime_escalations: Counter::new(),
            reconciliation_runs: Counter::new(),
            reconciliation_mismatches: Counter::new(),

            tick_to_trade_latency: LatencyRing::new(10_000),
            brain_signal_latency: LatencyRing::new(10_000),
            broker_ack_latency: LatencyRing::new(1_000),

            veto_counts: HashMap::new(),
        }
    }

    /// Record a veto reason (creates counter on first occurrence).
    pub fn record_veto(&mut self, reason: &str) {
        self.signals_vetoed.inc();
        self.veto_counts
            .entry(reason.to_string())
            .or_default()
            .inc();
    }

    /// Snapshot of all key metrics for logging/reporting.
    pub fn snapshot(&self) -> TelemetrySnapshot {
        self.snapshot_with_mode("UNKNOWN")
    }

    /// Snapshot with session mode included (P21).
    pub fn snapshot_with_mode(&self, session_mode: &str) -> TelemetrySnapshot {
        let t2t = self.tick_to_trade_latency.percentiles();
        TelemetrySnapshot {
            ticks_received: self.ticks_received.get(),
            ticks_filtered: self.ticks_filtered.get(),
            signals_generated: self.signals_generated.get(),
            signals_approved: self.signals_approved.get(),
            signals_vetoed: self.signals_vetoed.get(),
            orders_submitted: self.orders_submitted.get(),
            orders_filled: self.orders_filled.get(),
            t2t_p50_ms: t2t.p50_ms(),
            t2t_p95_ms: t2t.p95_ms(),
            t2t_p99_ms: t2t.p99_ms(),
            session_mode: session_mode.to_string(),
        }
    }

    /// Reset all counters (for daily rotation).
    pub fn reset_daily(&mut self) {
        self.ticks_received.reset();
        self.ticks_filtered.reset();
        self.ticks_routed_vanguard.reset();
        self.ticks_routed_apex.reset();
        self.ticks_dropped.reset();
        self.signals_generated.reset();
        self.signals_approved.reset();
        self.signals_vetoed.reset();
        self.orders_submitted.reset();
        self.orders_filled.reset();
        self.orders_cancelled.reset();
        self.orders_rejected.reset();
        self.exits_chandelier.reset();
        self.exits_eod.reset();
        self.exits_halt.reset();
        self.exits_dust.reset();
        self.regime_escalations.reset();
        self.reconciliation_runs.reset();
        self.reconciliation_mismatches.reset();
        self.veto_counts.clear();
    }
}

impl Default for Telemetry {
    fn default() -> Self {
        Self::new()
    }
}

/// Lightweight snapshot for logging/API exposure.
#[derive(Clone, Debug)]
pub struct TelemetrySnapshot {
    pub ticks_received: u64,
    pub ticks_filtered: u64,
    pub signals_generated: u64,
    pub signals_approved: u64,
    pub signals_vetoed: u64,
    pub orders_submitted: u64,
    pub orders_filled: u64,
    pub t2t_p50_ms: f64,
    pub t2t_p95_ms: f64,
    pub t2t_p99_ms: f64,
    /// P21: Current session mode (string representation).
    pub session_mode: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_counter_basic() {
        let c = Counter::new();
        assert_eq!(c.get(), 0);
        c.inc();
        assert_eq!(c.get(), 1);
        c.add(5);
        assert_eq!(c.get(), 6);
        c.reset();
        assert_eq!(c.get(), 0);
    }

    #[test]
    fn test_latency_ring_records_and_percentiles() {
        let ring = LatencyRing::new(100);
        // Record 100 samples: 1ms to 100ms
        for i in 1..=100 {
            ring.record(i * 1_000_000); // Convert ms to ns
        }
        let p = ring.percentiles();
        assert_eq!(p.count, 100);
        // P50 should be ~50ms
        assert!(p.p50_ms() >= 40.0 && p.p50_ms() <= 60.0);
        // P95 should be ~95ms
        assert!(p.p95_ms() >= 85.0 && p.p95_ms() <= 100.0);
    }

    #[test]
    fn test_latency_ring_empty() {
        let ring = LatencyRing::new(100);
        let p = ring.percentiles();
        assert_eq!(p.count, 0);
        assert_eq!(p.p50_ns, 0);
    }

    #[test]
    fn test_latency_ring_wraps() {
        let ring = LatencyRing::new(10);
        // Record 20 samples (wraps around)
        for i in 1..=20 {
            ring.record(i * 1_000_000);
        }
        let p = ring.percentiles();
        // Only last 10 samples survive (11-20ms)
        assert_eq!(p.count, 10);
    }

    #[test]
    fn test_telemetry_snapshot() {
        let telem = Telemetry::new();
        telem.ticks_received.add(1000);
        telem.signals_generated.add(50);
        telem.signals_approved.add(10);

        let snap = telem.snapshot();
        assert_eq!(snap.ticks_received, 1000);
        assert_eq!(snap.signals_generated, 50);
        assert_eq!(snap.signals_approved, 10);
        assert_eq!(snap.session_mode, "UNKNOWN");
    }

    #[test]
    fn test_telemetry_snapshot_with_mode() {
        let telem = Telemetry::new();
        telem.orders_submitted.inc();
        telem.orders_filled.inc();

        let snap = telem.snapshot_with_mode("MODE_B");
        assert_eq!(snap.orders_submitted, 1);
        assert_eq!(snap.orders_filled, 1);
        assert_eq!(snap.session_mode, "MODE_B");
    }

    #[test]
    fn test_telemetry_veto_tracking() {
        let mut telem = Telemetry::new();
        telem.record_veto("SpreadTooWide");
        telem.record_veto("SpreadTooWide");
        telem.record_veto("MaxPositionsReached");

        assert_eq!(telem.signals_vetoed.get(), 3);
        assert_eq!(telem.veto_counts["SpreadTooWide"].get(), 2);
        assert_eq!(telem.veto_counts["MaxPositionsReached"].get(), 1);
    }

    #[test]
    fn test_telemetry_daily_reset() {
        let mut telem = Telemetry::new();
        telem.ticks_received.add(5000);
        telem.record_veto("StaleData");
        assert_eq!(telem.ticks_received.get(), 5000);

        telem.reset_daily();
        assert_eq!(telem.ticks_received.get(), 0);
        assert!(telem.veto_counts.is_empty());
    }
}
