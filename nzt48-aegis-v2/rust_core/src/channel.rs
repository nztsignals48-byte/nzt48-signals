//! Crossbeam bounded channel with oldest-tick dropping and health monitoring.
//! Capacity: 50,000 (configurable). Oldest dropped when full, newest preserved.
//! Drop rate monitoring: >100 drops/sec → REDUCE escalation.
//! Queue depth: 40,000 → REDUCE, 50,000 → HALT.

use crate::types::{MarketTick, RiskRegime, TickerId};

/// Configuration for the tick channel.
#[derive(Clone, Debug)]
pub struct ChannelConfig {
    /// Channel capacity (default: 50,000).
    pub capacity: usize,
    /// Queue depth threshold for REDUCE (default: 40,000 = 80%).
    pub reduce_threshold: usize,
    /// Queue depth threshold for HALT (default: 50,000 = 100%).
    pub halt_threshold: usize,
    /// Drop rate per second that triggers REDUCE (default: 100).
    pub drop_alert_per_sec: u64,
}

impl Default for ChannelConfig {
    fn default() -> Self {
        Self {
            capacity: 50_000,
            reduce_threshold: 40_000,
            halt_threshold: 50_000,
            drop_alert_per_sec: 100,
        }
    }
}

/// Monitoring state for the channel.
#[derive(Clone, Debug)]
pub struct ChannelMonitor {
    /// Total ticks dropped since creation.
    pub total_drops: u64,
    /// Drops in the current 1-second window.
    pub drops_this_second: u64,
    /// Start of the current 1-second window (nanoseconds).
    pub window_start_ns: u64,
}

impl ChannelMonitor {
    fn new() -> Self {
        Self {
            total_drops: 0,
            drops_this_second: 0,
            window_start_ns: 0,
        }
    }

    /// Record a drop event, rolling the window if needed.
    fn record_drop(&mut self, now_ns: u64) {
        self.total_drops += 1;
        if now_ns >= self.window_start_ns + 1_000_000_000 {
            self.drops_this_second = 1;
            self.window_start_ns = now_ns;
        } else {
            self.drops_this_second += 1;
        }
    }
}

/// Bounded tick channel with oldest-dropping and health monitoring.
pub struct TickChannel {
    pub config: ChannelConfig,
    sender: crossbeam_channel::Sender<MarketTick>,
    receiver: crossbeam_channel::Receiver<MarketTick>,
    pub monitor: ChannelMonitor,
}

impl TickChannel {
    pub fn new(config: ChannelConfig) -> Self {
        let (sender, receiver) = crossbeam_channel::bounded(config.capacity);
        Self {
            config,
            sender,
            receiver,
            monitor: ChannelMonitor::new(),
        }
    }

    /// Send a tick, dropping the oldest if the channel is full.
    /// Returns true if the tick was sent, false if it was sent after dropping oldest.
    pub fn send_or_drop_oldest(&mut self, tick: MarketTick, now_ns: u64) -> bool {
        match self.sender.try_send(tick.clone()) {
            Ok(()) => true,
            Err(crossbeam_channel::TrySendError::Full(_)) => {
                // Drop oldest tick
                let _ = self.receiver.try_recv();
                self.monitor.record_drop(now_ns);
                // Now send the new tick (should succeed since we just freed a slot)
                let _ = self.sender.try_send(tick);
                false
            }
            Err(crossbeam_channel::TrySendError::Disconnected(_)) => false,
        }
    }

    /// Current number of items in the channel.
    pub fn len(&self) -> usize {
        self.sender.len()
    }

    /// Whether the channel is empty.
    pub fn is_empty(&self) -> bool {
        self.sender.is_empty()
    }

    /// Receive a single tick (non-blocking).
    pub fn try_recv(&self) -> Option<MarketTick> {
        self.receiver.try_recv().ok()
    }

    /// Receive a batch of ticks (up to max_batch, non-blocking).
    pub fn recv_batch(&self, max_batch: usize) -> Vec<MarketTick> {
        let mut batch = Vec::with_capacity(max_batch);
        for _ in 0..max_batch {
            match self.receiver.try_recv() {
                Ok(tick) => batch.push(tick),
                Err(_) => break,
            }
        }
        batch
    }

    /// Check channel health against thresholds.
    /// Returns the escalation regime if thresholds are breached, or None.
    pub fn check_health(&self) -> Option<RiskRegime> {
        let depth = self.len();
        if depth >= self.config.halt_threshold {
            return Some(RiskRegime::Halt);
        }
        if depth >= self.config.reduce_threshold {
            return Some(RiskRegime::Reduce);
        }
        if self.monitor.drops_this_second >= self.config.drop_alert_per_sec {
            return Some(RiskRegime::Reduce);
        }
        None
    }
}

/// SC-09: Dual-path overflow handling for tick channel saturation.
/// (a) OFI path: invalidate quote imbalance signals + suspend QI EWMA
/// (b) Chandelier path: aggregate high/low/volume into current bar
/// VPIN scoring suppressed for 30s after overflow event.
pub struct DualPathOverflow {
    /// Per-ticker dropped count during current overflow window.
    dropped_counts: std::collections::HashMap<TickerId, u32>,
    /// Per-ticker aggregated high during overflow (Chandelier path).
    agg_high: std::collections::HashMap<TickerId, f64>,
    /// Per-ticker aggregated low during overflow (Chandelier path).
    agg_low: std::collections::HashMap<TickerId, f64>,
    /// Per-ticker aggregated volume during overflow (Chandelier path).
    agg_volume: std::collections::HashMap<TickerId, u64>,
    /// Timestamp when VPIN suppression ends (30s after last overflow).
    pub vpin_suppressed_until_ns: u64,
}

impl DualPathOverflow {
    pub fn new() -> Self {
        Self {
            dropped_counts: std::collections::HashMap::new(),
            agg_high: std::collections::HashMap::new(),
            agg_low: std::collections::HashMap::new(),
            agg_volume: std::collections::HashMap::new(),
            vpin_suppressed_until_ns: 0,
        }
    }

    /// Record a dropped tick. Updates both OFI and Chandelier paths.
    pub fn record_drop(&mut self, tick: &MarketTick, now_ns: u64) {
        // OFI path: count drops per ticker
        *self.dropped_counts.entry(tick.ticker_id).or_insert(0) += 1;

        // Chandelier path: aggregate H/L/V
        let high = self.agg_high.entry(tick.ticker_id).or_insert(0.0);
        if tick.last > *high {
            *high = tick.last;
        }
        let low = self.agg_low.entry(tick.ticker_id).or_insert(f64::MAX);
        if tick.last < *low {
            *low = tick.last;
        }
        *self.agg_volume.entry(tick.ticker_id).or_insert(0) += tick.volume;

        // Suppress VPIN for 30s after any drop
        self.vpin_suppressed_until_ns = now_ns + 30_000_000_000;
    }

    /// Check if VPIN is currently suppressed.
    pub fn is_vpin_suppressed(&self, now_ns: u64) -> bool {
        now_ns < self.vpin_suppressed_until_ns
    }

    /// Drain OFI invalidation data for a ticker (returns dropped count, resets to 0).
    pub fn drain_ofi_drops(&mut self, ticker_id: TickerId) -> u32 {
        self.dropped_counts.remove(&ticker_id).unwrap_or(0)
    }

    /// Drain aggregated bar data for a ticker (Chandelier path).
    /// Returns (high, low, volume) or None if no data.
    pub fn drain_aggregated_bar(&mut self, ticker_id: TickerId) -> Option<(f64, f64, u64)> {
        let high = self.agg_high.remove(&ticker_id)?;
        let low = self.agg_low.remove(&ticker_id).unwrap_or(high);
        let vol = self.agg_volume.remove(&ticker_id).unwrap_or(0);
        Some((high, low, vol))
    }

    /// Total drops across all tickers.
    pub fn total_drops(&self) -> u32 {
        self.dropped_counts.values().sum()
    }

    /// Reset all overflow state.
    pub fn reset(&mut self) {
        self.dropped_counts.clear();
        self.agg_high.clear();
        self.agg_low.clear();
        self.agg_volume.clear();
    }
}

impl Default for DualPathOverflow {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_tick(ticker_id: u32, last: f64, volume: u64) -> MarketTick {
        MarketTick {
            timestamp_ns: 0,
            recv_timestamp_ns: 0,
            volume,
            bid: last - 0.01,
            ask: last + 0.01,
            last,
            ticker_id: TickerId(ticker_id),
        }
    }

    #[test]
    fn test_dual_path_overflow_basic() {
        let mut dpo = DualPathOverflow::new();
        let tick1 = make_tick(1, 10.50, 100);
        let tick2 = make_tick(1, 10.80, 200);
        let tick3 = make_tick(1, 10.20, 150);

        dpo.record_drop(&tick1, 1_000_000_000);
        dpo.record_drop(&tick2, 1_000_000_001);
        dpo.record_drop(&tick3, 1_000_000_002);

        // OFI path: 3 drops for ticker 1
        assert_eq!(dpo.drain_ofi_drops(TickerId(1)), 3);
        assert_eq!(dpo.drain_ofi_drops(TickerId(1)), 0); // Drained

        // Chandelier path: aggregated bar from first 3 drops
        // High=10.80, Low=10.20, Vol=100+200+150=450
        let (high, low, vol) = dpo.drain_aggregated_bar(TickerId(1)).expect("has data");
        assert!((high - 10.80).abs() < 0.001);
        assert!((low - 10.20).abs() < 0.001);
        assert_eq!(vol, 450);
    }

    #[test]
    fn test_vpin_suppression() {
        let mut dpo = DualPathOverflow::new();
        let tick = make_tick(1, 10.0, 100);

        assert!(!dpo.is_vpin_suppressed(1_000_000_000));
        dpo.record_drop(&tick, 1_000_000_000);
        // Suppressed for 30s after drop
        assert!(dpo.is_vpin_suppressed(1_000_000_000 + 29_000_000_000));
        assert!(!dpo.is_vpin_suppressed(1_000_000_000 + 31_000_000_000));
    }

    #[test]
    fn test_dual_path_multi_ticker() {
        let mut dpo = DualPathOverflow::new();
        dpo.record_drop(&make_tick(1, 10.0, 100), 1_000_000_000);
        dpo.record_drop(&make_tick(2, 20.0, 200), 1_000_000_000);
        dpo.record_drop(&make_tick(1, 11.0, 50), 1_000_000_001);

        assert_eq!(dpo.total_drops(), 3);
        assert_eq!(dpo.drain_ofi_drops(TickerId(1)), 2);
        assert_eq!(dpo.drain_ofi_drops(TickerId(2)), 1);
    }
}
