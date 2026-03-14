//! Phase 22: Institutional hardening — panic handling, circuit breakers,
//! health checks, and system resilience infrastructure.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

/// Global panic guard: catches panics in hot-path threads and triggers HALT.
/// Shared across all threads via Arc.
#[derive(Clone)]
pub struct PanicGuard {
    /// Set to true when any thread panics.
    panicked: Arc<AtomicBool>,
    /// Timestamp of the panic (nanoseconds).
    panic_ns: Arc<AtomicU64>,
    /// Human-readable panic message (stored externally, not in atomic).
    panic_count: Arc<AtomicU64>,
}

impl PanicGuard {
    pub fn new() -> Self {
        Self {
            panicked: Arc::new(AtomicBool::new(false)),
            panic_ns: Arc::new(AtomicU64::new(0)),
            panic_count: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Record a panic event.
    pub fn record_panic(&self, now_ns: u64) {
        self.panicked.store(true, Ordering::SeqCst);
        self.panic_ns.store(now_ns, Ordering::SeqCst);
        self.panic_count.fetch_add(1, Ordering::SeqCst);
    }

    /// Check if any panic has occurred.
    pub fn has_panicked(&self) -> bool {
        self.panicked.load(Ordering::SeqCst)
    }

    /// Total panic count.
    pub fn panic_count(&self) -> u64 {
        self.panic_count.load(Ordering::SeqCst)
    }

    /// Timestamp of last panic.
    pub fn last_panic_ns(&self) -> u64 {
        self.panic_ns.load(Ordering::SeqCst)
    }

    /// Clear panic state (after manual review + HALT cleared).
    pub fn clear(&self) {
        self.panicked.store(false, Ordering::SeqCst);
    }
}

impl Default for PanicGuard {
    fn default() -> Self {
        Self::new()
    }
}

/// Circuit breaker: rate-limits actions when error rate exceeds threshold.
/// Auto-resets after cooldown period.
pub struct CircuitBreaker {
    /// Error count in current window.
    errors: u64,
    /// Window start (nanoseconds).
    window_start_ns: u64,
    /// Maximum errors per window before tripping.
    max_errors: u64,
    /// Window size (nanoseconds).
    window_ns: u64,
    /// Cooldown after tripping (nanoseconds).
    cooldown_ns: u64,
    /// When the breaker was tripped (0 = not tripped).
    tripped_ns: u64,
}

impl CircuitBreaker {
    /// Create a new circuit breaker.
    /// `max_errors`: errors allowed per window.
    /// `window_secs`: window duration in seconds.
    /// `cooldown_secs`: cooldown after tripping in seconds.
    pub fn new(max_errors: u64, window_secs: u64, cooldown_secs: u64) -> Self {
        Self {
            errors: 0,
            window_start_ns: 0,
            max_errors,
            window_ns: window_secs * 1_000_000_000,
            cooldown_ns: cooldown_secs * 1_000_000_000,
            tripped_ns: 0,
        }
    }

    /// Record an error. Returns true if the breaker just tripped.
    pub fn record_error(&mut self, now_ns: u64) -> bool {
        self.maybe_reset_window(now_ns);
        self.errors += 1;
        if self.errors >= self.max_errors && self.tripped_ns == 0 {
            self.tripped_ns = now_ns;
            return true;
        }
        false
    }

    /// Is the breaker currently tripped (in cooldown)?
    pub fn is_tripped(&self, now_ns: u64) -> bool {
        if self.tripped_ns == 0 {
            return false;
        }
        now_ns < self.tripped_ns + self.cooldown_ns
    }

    /// Is the action allowed right now?
    pub fn is_allowed(&self, now_ns: u64) -> bool {
        !self.is_tripped(now_ns)
    }

    /// Reset if window has elapsed.
    fn maybe_reset_window(&mut self, now_ns: u64) {
        if now_ns >= self.window_start_ns + self.window_ns {
            self.errors = 0;
            self.window_start_ns = now_ns;
            // Auto-reset trip if cooldown has passed
            if self.tripped_ns > 0 && now_ns >= self.tripped_ns + self.cooldown_ns {
                self.tripped_ns = 0;
            }
        }
    }

    /// Manual reset (after investigation).
    pub fn reset(&mut self) {
        self.errors = 0;
        self.tripped_ns = 0;
    }

    pub fn error_count(&self) -> u64 {
        self.errors
    }
}

/// System health check: aggregates multiple subsystem health signals.
#[derive(Clone, Debug)]
pub struct HealthCheck {
    pub broker_connected: bool,
    pub wal_healthy: bool,
    pub python_bridge_alive: bool,
    pub disk_space_ok: bool,
    pub clock_synced: bool,
    pub last_tick_age_secs: u64,
    pub channel_depth_pct: f64,
    pub memory_usage_mb: u64,
}

impl HealthCheck {
    /// Is the system healthy enough to trade?
    pub fn is_trading_ready(&self) -> bool {
        self.broker_connected
            && self.wal_healthy
            && self.clock_synced
            && self.disk_space_ok
            && self.last_tick_age_secs < 120
            && self.channel_depth_pct < 90.0
    }

    /// Is the system in a degraded but survivable state?
    /// Returns false if critical failures exist (broker down, WAL down, stale data, etc.)
    pub fn is_degraded(&self) -> bool {
        !self.is_trading_ready()
            && self.broker_connected
            && self.wal_healthy
            && self.disk_space_ok
            && self.clock_synced
            && self.last_tick_age_secs < 120
    }

    /// Critical failures that require immediate HALT.
    pub fn critical_failures(&self) -> Vec<&'static str> {
        let mut failures = Vec::new();
        if !self.broker_connected {
            failures.push("Broker disconnected");
        }
        if !self.wal_healthy {
            failures.push("WAL unavailable");
        }
        if !self.disk_space_ok {
            failures.push("Disk space critical");
        }
        if !self.clock_synced {
            failures.push("Clock not synced");
        }
        if self.last_tick_age_secs >= 120 {
            failures.push("Market data stale (>120s)");
        }
        failures
    }
}

impl Default for HealthCheck {
    fn default() -> Self {
        Self {
            broker_connected: true,
            wal_healthy: true,
            python_bridge_alive: true,
            disk_space_ok: true,
            clock_synced: true,
            last_tick_age_secs: 0,
            channel_depth_pct: 0.0,
            memory_usage_mb: 0,
        }
    }
}

/// Watchdog timer: triggers action if not reset within timeout.
pub struct Watchdog {
    last_feed_ns: u64,
    timeout_ns: u64,
    /// Number of times the watchdog has expired.
    expirations: u64,
}

impl Watchdog {
    pub fn new(timeout_secs: u64) -> Self {
        Self {
            last_feed_ns: 0,
            timeout_ns: timeout_secs * 1_000_000_000,
            expirations: 0,
        }
    }

    /// Feed the watchdog (reset timer).
    pub fn feed(&mut self, now_ns: u64) {
        self.last_feed_ns = now_ns;
    }

    /// Check if the watchdog has expired.
    pub fn is_expired(&mut self, now_ns: u64) -> bool {
        if self.last_feed_ns == 0 {
            self.last_feed_ns = now_ns;
            return false;
        }
        if now_ns > self.last_feed_ns + self.timeout_ns {
            self.expirations += 1;
            true
        } else {
            false
        }
    }

    pub fn expirations(&self) -> u64 {
        self.expirations
    }
}

/// Rate limiter for specific operations (e.g., WAL writes, API calls).
pub struct RateLimiter {
    /// Maximum operations per window.
    max_ops: u64,
    /// Current operation count.
    ops: u64,
    /// Window start (nanoseconds).
    window_start_ns: u64,
    /// Window size (nanoseconds).
    window_ns: u64,
}

impl RateLimiter {
    pub fn new(max_ops_per_sec: u64) -> Self {
        Self {
            max_ops: max_ops_per_sec,
            ops: 0,
            window_start_ns: 0,
            window_ns: 1_000_000_000, // 1 second window
        }
    }

    /// Try to consume one operation. Returns true if allowed.
    pub fn try_acquire(&mut self, now_ns: u64) -> bool {
        if now_ns >= self.window_start_ns + self.window_ns {
            self.ops = 0;
            self.window_start_ns = now_ns;
        }
        if self.ops < self.max_ops {
            self.ops += 1;
            true
        } else {
            false
        }
    }

    pub fn remaining(&self) -> u64 {
        self.max_ops.saturating_sub(self.ops)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_panic_guard_basic() {
        let guard = PanicGuard::new();
        assert!(!guard.has_panicked());
        guard.record_panic(1_000_000_000);
        assert!(guard.has_panicked());
        assert_eq!(guard.panic_count(), 1);
        assert_eq!(guard.last_panic_ns(), 1_000_000_000);
        guard.clear();
        assert!(!guard.has_panicked());
    }

    #[test]
    fn test_panic_guard_clone_shares_state() {
        let guard = PanicGuard::new();
        let clone = guard.clone();
        guard.record_panic(1_000_000_000);
        assert!(clone.has_panicked());
    }

    #[test]
    fn test_circuit_breaker_trips() {
        let mut cb = CircuitBreaker::new(3, 10, 5);
        let t = 1_000_000_000u64;
        assert!(!cb.record_error(t));
        assert!(!cb.record_error(t));
        assert!(cb.record_error(t)); // 3rd error = trip
        assert!(cb.is_tripped(t));
        assert!(!cb.is_allowed(t));
    }

    #[test]
    fn test_circuit_breaker_cooldown_recovery() {
        let mut cb = CircuitBreaker::new(3, 10, 5);
        let t = 1_000_000_000u64;
        cb.record_error(t);
        cb.record_error(t);
        cb.record_error(t);
        assert!(cb.is_tripped(t));
        // After 5s cooldown
        assert!(!cb.is_tripped(t + 6_000_000_000));
        assert!(cb.is_allowed(t + 6_000_000_000));
    }

    #[test]
    fn test_circuit_breaker_manual_reset() {
        let mut cb = CircuitBreaker::new(3, 10, 5);
        let t = 1_000_000_000u64;
        cb.record_error(t);
        cb.record_error(t);
        cb.record_error(t);
        assert!(cb.is_tripped(t));
        cb.reset();
        assert!(!cb.is_tripped(t));
        assert_eq!(cb.error_count(), 0);
    }

    #[test]
    fn test_health_check_trading_ready() {
        let hc = HealthCheck::default();
        assert!(hc.is_trading_ready());
        assert!(!hc.is_degraded());
        assert!(hc.critical_failures().is_empty());
    }

    #[test]
    fn test_health_check_degraded() {
        let hc = HealthCheck {
            python_bridge_alive: false,
            last_tick_age_secs: 130, // Stale
            ..Default::default()
        };
        assert!(!hc.is_trading_ready());
        assert!(!hc.is_degraded()); // Too degraded (stale data)
        let failures = hc.critical_failures();
        assert!(failures.contains(&"Market data stale (>120s)"));
    }

    #[test]
    fn test_health_check_critical() {
        let hc = HealthCheck {
            broker_connected: false,
            wal_healthy: false,
            ..Default::default()
        };
        let failures = hc.critical_failures();
        assert_eq!(failures.len(), 2);
    }

    #[test]
    fn test_watchdog_feed_and_expire() {
        let mut wd = Watchdog::new(5);
        wd.feed(1_000_000_000);
        assert!(!wd.is_expired(3_000_000_000)); // 2s < 5s
        assert!(wd.is_expired(7_000_000_000)); // 6s > 5s
        assert_eq!(wd.expirations(), 1);
    }

    #[test]
    fn test_watchdog_first_check_not_expired() {
        let mut wd = Watchdog::new(5);
        // First check initializes and doesn't expire
        assert!(!wd.is_expired(1_000_000_000));
    }

    #[test]
    fn test_rate_limiter_basic() {
        let mut rl = RateLimiter::new(3);
        let t = 1_000_000_000u64;
        assert!(rl.try_acquire(t));
        assert!(rl.try_acquire(t));
        assert!(rl.try_acquire(t));
        assert!(!rl.try_acquire(t)); // Exhausted
        assert_eq!(rl.remaining(), 0);
        // New window
        assert!(rl.try_acquire(t + 2_000_000_000));
    }
}
