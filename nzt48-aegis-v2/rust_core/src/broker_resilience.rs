//! Broker Connection Resilience — Phase 17
//!
//! Monitors IB Gateway health: heartbeat tracking, disconnect detection,
//! fill error rate gating, and exponential-backoff reconnect scheduling.

use std::collections::VecDeque;

const WINDOW_CAP: usize = 100;
const NANOS_PER_SEC: u64 = 1_000_000_000;
const ONE_MINUTE_NS: u64 = 60 * NANOS_PER_SEC;
const DEFAULT_MAX_DISCONNECT_SECS: u64 = 120;
const DEFAULT_MAX_FILL_ERROR_RATE_PCT: f64 = 5.0;
const MAX_BACKOFF_MS: u64 = 30_000;
const BASE_BACKOFF_MS: u64 = 1_000;
const JITTER_MOD: u64 = 1_000;
const JITTER_MULT: u64 = 137;

/// Tracks broker connection health and decides when to reduce exposure or halt.
pub struct BrokerHealthMonitor {
    pub last_heartbeat_ns: u64,
    pub disconnect_start_ns: Option<u64>,
    pub reconnect_attempts: u32,
    pub fill_errors_window: VecDeque<u64>,
    pub fills_window: VecDeque<u64>,
    pub max_disconnect_secs: u64,
    pub max_fill_error_rate_pct: f64,
}

impl BrokerHealthMonitor {
    /// Create a new monitor with default thresholds.
    #[must_use]
    pub fn new() -> Self {
        Self {
            last_heartbeat_ns: 0,
            disconnect_start_ns: None,
            reconnect_attempts: 0,
            fill_errors_window: VecDeque::with_capacity(WINDOW_CAP),
            fills_window: VecDeque::with_capacity(WINDOW_CAP),
            max_disconnect_secs: DEFAULT_MAX_DISCONNECT_SECS,
            max_fill_error_rate_pct: DEFAULT_MAX_FILL_ERROR_RATE_PCT,
        }
    }

    /// Record a successful heartbeat, clearing any disconnect state.
    pub fn record_heartbeat(&mut self, now_ns: u64) {
        self.last_heartbeat_ns = now_ns;
        self.disconnect_start_ns = None;
    }

    /// Record a disconnect event. Only sets the start time on first call.
    pub fn record_disconnect(&mut self, now_ns: u64) {
        if self.disconnect_start_ns.is_none() {
            self.disconnect_start_ns = Some(now_ns);
        }
    }

    /// True if we have been disconnected longer than `max_disconnect_secs`.
    #[must_use]
    pub fn is_disconnected_too_long(&self, now_ns: u64) -> bool {
        match self.disconnect_start_ns {
            Some(start) => {
                let elapsed_secs = now_ns.saturating_sub(start) / NANOS_PER_SEC;
                elapsed_secs > self.max_disconnect_secs
            }
            None => false,
        }
    }

    /// Record a successful fill.
    pub fn record_fill_success(&mut self, now_ns: u64) {
        push_bounded(&mut self.fills_window, now_ns);
    }

    /// Record a fill error.
    pub fn record_fill_error(&mut self, now_ns: u64) {
        push_bounded(&mut self.fill_errors_window, now_ns);
    }

    /// Percentage of fill errors in the last 60 seconds.
    /// Returns 0.0 if no fills or errors in the window.
    #[must_use]
    pub fn fill_error_rate_1min(&self, now_ns: u64) -> f64 {
        let cutoff = now_ns.saturating_sub(ONE_MINUTE_NS);
        let errors = count_since(&self.fill_errors_window, cutoff);
        let successes = count_since(&self.fills_window, cutoff);
        let total = errors + successes;
        if total == 0 {
            return 0.0;
        }
        (errors as f64 / total as f64) * 100.0
    }

    /// True if fill error rate exceeds the configured threshold — reduce exposure.
    #[must_use]
    pub fn should_reduce(&self, now_ns: u64) -> bool {
        self.fill_error_rate_1min(now_ns) > self.max_fill_error_rate_pct
    }

    /// True if disconnected too long — halt all trading.
    #[must_use]
    pub fn should_halt(&self, now_ns: u64) -> bool {
        self.is_disconnected_too_long(now_ns)
    }

    /// Exponential backoff with deterministic jitter.
    /// `min(1000 * 2^attempts, 30000) + (attempts * 137 % 1000)`
    #[must_use]
    pub fn next_reconnect_delay_ms(&self) -> u64 {
        let exp = BASE_BACKOFF_MS.saturating_mul(
            1u64.checked_shl(self.reconnect_attempts).unwrap_or(u64::MAX),
        );
        let base = exp.min(MAX_BACKOFF_MS);
        let jitter = (u64::from(self.reconnect_attempts)).wrapping_mul(JITTER_MULT) % JITTER_MOD;
        base.saturating_add(jitter)
    }

    /// Increment the reconnect attempt counter.
    pub fn record_reconnect_attempt(&mut self) {
        self.reconnect_attempts = self.reconnect_attempts.saturating_add(1);
    }

    /// Reset reconnect attempts after a successful connection.
    pub fn reset_reconnect(&mut self) {
        self.reconnect_attempts = 0;
    }
}

impl Default for BrokerHealthMonitor {
    fn default() -> Self {
        Self::new()
    }
}

/// Push a timestamp into a bounded deque, evicting the oldest if at capacity.
fn push_bounded(deque: &mut VecDeque<u64>, ts: u64) {
    if deque.len() >= WINDOW_CAP {
        deque.pop_front();
    }
    deque.push_back(ts);
}

/// Count entries in the deque that are >= cutoff.
fn count_since(deque: &VecDeque<u64>, cutoff: u64) -> usize {
    deque.iter().filter(|&&t| t >= cutoff).count()
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const SEC: u64 = NANOS_PER_SEC;

    #[test]
    fn heartbeat_clears_disconnect() {
        let mut m = BrokerHealthMonitor::new();
        let t0 = 1_000 * SEC;
        m.record_disconnect(t0);
        assert!(m.disconnect_start_ns.is_some());

        m.record_heartbeat(t0 + 5 * SEC);
        assert!(m.disconnect_start_ns.is_none());
        assert_eq!(m.last_heartbeat_ns, t0 + 5 * SEC);
    }

    #[test]
    fn disconnect_not_too_long_within_threshold() {
        let mut m = BrokerHealthMonitor::new();
        let t0 = 1_000 * SEC;
        m.record_disconnect(t0);
        // 60 seconds later — still within 120s default
        assert!(!m.is_disconnected_too_long(t0 + 60 * SEC));
    }

    #[test]
    fn disconnect_too_long_beyond_threshold() {
        let mut m = BrokerHealthMonitor::new();
        let t0 = 1_000 * SEC;
        m.record_disconnect(t0);
        // 121 seconds later — beyond 120s
        assert!(m.is_disconnected_too_long(t0 + 121 * SEC));
        assert!(m.should_halt(t0 + 121 * SEC));
    }

    #[test]
    fn disconnect_idempotent() {
        let mut m = BrokerHealthMonitor::new();
        let t0 = 1_000 * SEC;
        m.record_disconnect(t0);
        m.record_disconnect(t0 + 10 * SEC); // should NOT overwrite
        assert_eq!(m.disconnect_start_ns, Some(t0));
    }

    #[test]
    fn fill_error_rate_calculation() {
        let mut m = BrokerHealthMonitor::new();
        let now = 100 * SEC;

        // 9 successes, 1 error in last minute → 10% error rate
        for i in 0..9 {
            m.record_fill_success(now - (i * SEC));
        }
        m.record_fill_error(now - SEC);

        let rate = m.fill_error_rate_1min(now);
        assert!((rate - 10.0).abs() < 0.01, "expected ~10%, got {rate}");
        assert!(m.should_reduce(now)); // 10% > 5% threshold
    }

    #[test]
    fn fill_error_rate_zero_when_empty() {
        let m = BrokerHealthMonitor::new();
        assert!((m.fill_error_rate_1min(100 * SEC)).abs() < f64::EPSILON);
    }

    #[test]
    fn fill_error_rate_ignores_old_entries() {
        let mut m = BrokerHealthMonitor::new();
        let now = 200 * SEC;

        // Old error (2 minutes ago) — should be excluded
        m.record_fill_error(now - 120 * SEC);
        // Recent success
        m.record_fill_success(now - 5 * SEC);

        let rate = m.fill_error_rate_1min(now);
        assert!(rate.abs() < f64::EPSILON, "old error should be excluded, got {rate}");
    }

    #[test]
    fn exponential_backoff_with_jitter() {
        let mut m = BrokerHealthMonitor::new();

        // attempt 0: 1000 * 2^0 + 0*137%1000 = 1000
        assert_eq!(m.next_reconnect_delay_ms(), 1000);

        m.record_reconnect_attempt(); // attempt 1
        // 1000 * 2^1 + 1*137%1000 = 2000 + 137 = 2137
        assert_eq!(m.next_reconnect_delay_ms(), 2137);

        m.record_reconnect_attempt(); // attempt 2
        // 1000 * 2^2 + 2*137%1000 = 4000 + 274 = 4274
        assert_eq!(m.next_reconnect_delay_ms(), 4274);

        m.record_reconnect_attempt(); // attempt 3
        // 1000 * 2^3 + 3*137%1000 = 8000 + 411 = 8411
        assert_eq!(m.next_reconnect_delay_ms(), 8411);

        m.record_reconnect_attempt(); // attempt 4
        // 1000 * 2^4 + 4*137%1000 = 16000 + 548 = 16548
        assert_eq!(m.next_reconnect_delay_ms(), 16548);

        m.record_reconnect_attempt(); // attempt 5
        // 1000 * 2^5 = 32000 → capped at 30000 + 5*137%1000 = 30000 + 685 = 30685
        assert_eq!(m.next_reconnect_delay_ms(), 30685);
    }

    #[test]
    fn reconnect_reset() {
        let mut m = BrokerHealthMonitor::new();
        m.record_reconnect_attempt();
        m.record_reconnect_attempt();
        assert_eq!(m.reconnect_attempts, 2);

        m.reset_reconnect();
        assert_eq!(m.reconnect_attempts, 0);
        assert_eq!(m.next_reconnect_delay_ms(), 1000);
    }

    #[test]
    fn window_bounded_at_100() {
        let mut m = BrokerHealthMonitor::new();
        for i in 0..150 {
            m.record_fill_success(i as u64);
            m.record_fill_error(i as u64);
        }
        assert_eq!(m.fills_window.len(), WINDOW_CAP);
        assert_eq!(m.fill_errors_window.len(), WINDOW_CAP);
    }

    #[test]
    fn no_halt_when_not_disconnected() {
        let m = BrokerHealthMonitor::new();
        assert!(!m.should_halt(999 * SEC));
    }

    #[test]
    fn high_attempts_dont_overflow() {
        let mut m = BrokerHealthMonitor::new();
        m.reconnect_attempts = 100;
        // Should not panic, should be capped at max + jitter
        let delay = m.next_reconnect_delay_ms();
        assert!(delay >= MAX_BACKOFF_MS);
    }
}
