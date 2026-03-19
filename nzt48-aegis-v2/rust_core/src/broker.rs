//! Broker adapter trait + supporting types. Synchronous interface.
//! Async wrapping via tokio happens in Phase 8.

use std::fmt;

use crate::types::{BrokerAckStatus, OrderSide, TickerId};

/// Errors from broker operations.
#[derive(Debug)]
pub enum BrokerError {
    DuplicateOrderId(String),
    RateLimitExceeded,
    NotConnected,
    OrderNotFound(String),
    InvalidOrder(String),
}

impl fmt::Display for BrokerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            BrokerError::DuplicateOrderId(id) => write!(f, "Duplicate order_id: {id}"),
            BrokerError::RateLimitExceeded => write!(f, "Rate limit exceeded (H16)"),
            BrokerError::NotConnected => write!(f, "Broker not connected"),
            BrokerError::OrderNotFound(id) => write!(f, "Order not found: {id}"),
            BrokerError::InvalidOrder(msg) => write!(f, "Invalid order: {msg}"),
        }
    }
}

/// Events emitted by the broker adapter.
#[derive(Debug, Clone)]
pub enum BrokerEvent {
    Ack {
        order_id: String,
        ibkr_order_id: i64,
        status: BrokerAckStatus,
        message: Option<String>,
    },
    Fill {
        order_id: String,
        ticker_id: TickerId,
        filled_qty: u32,
        remaining_qty: u32,
        price: f64,
        exec_id: String,
        commission: f64,
    },
    Disconnected,
    Connected {
        next_valid_id: u64,
    },
}

/// A broker-reported position (for reconciliation).
#[derive(Debug, Clone)]
pub struct BrokerPosition {
    pub ticker_id: TickerId,
    pub qty: u32,
    pub avg_cost: f64,
}

/// A broker-reported open order (for reconciliation).
#[derive(Debug, Clone)]
pub struct BrokerOpenOrder {
    pub order_id: String,
    pub ibkr_order_id: i64,
    pub ticker_id: TickerId,
    pub qty: u32,
    pub status: String,
}

/// Synchronous broker adapter trait.
/// Phase 8 wraps this in async for tokio integration.
pub trait BrokerAdapter {
    /// Submit a new order. Returns error on duplicate, rate limit, or disconnect.
    fn submit_order(
        &mut self,
        order_id: &str,
        ticker_id: TickerId,
        side: OrderSide,
        qty: u32,
        limit_price: f64,
    ) -> Result<(), BrokerError>;

    /// Request cancellation of an existing order (H54).
    fn cancel_order(&mut self, order_id: &str) -> Result<(), BrokerError>;

    /// Query broker for current positions (reconciliation).
    fn request_positions(&self) -> Result<Vec<BrokerPosition>, BrokerError>;

    /// Query broker for open orders (reconciliation).
    fn request_open_orders(&self) -> Result<Vec<BrokerOpenOrder>, BrokerError>;

    /// Send heartbeat. Must be called regularly to maintain connection.
    fn heartbeat(&mut self) -> Result<(), BrokerError>;

    /// Connection status. Checks both link state and heartbeat freshness.
    fn is_connected(&self) -> bool;

    /// Drain pending broker events (acks, fills, cancels).
    fn drain_events(&mut self) -> Vec<BrokerEvent>;

    /// Current nextValidId from broker (H47).
    fn next_valid_id(&self) -> u64;

    /// Client ID for IBKR session isolation (H41).
    fn client_id(&self) -> u32;

    /// Re-subscribe to all market data streams (bars + mktdata).
    /// Called after broker reconnection (Error 1102) to restore data feeds.
    /// Default: no-op for brokers that don't support subscriptions.
    fn resubscribe_all(&mut self) -> u32 {
        0
    }

    /// P21: Subscribe to L1 bid/ask for a set of tickers (mode rotation).
    fn subscribe_l1_batch(&mut self, ticker_ids: &[TickerId]) -> u32 {
        // Default: no-op for brokers that don't support subscriptions
        let _ = ticker_ids;
        0
    }

    /// P21: Unsubscribe from L1 bid/ask for a set of tickers (mode rotation).
    fn unsubscribe_l1_batch(&mut self, ticker_ids: &[TickerId]) -> u32 {
        // Default: no-op for brokers that don't support subscriptions
        let _ = ticker_ids;
        0
    }

    /// P21: Get current count of active L1 subscriptions (telemetry).
    fn l1_subscription_count(&self) -> u32 {
        0
    }

    /// P21: Check if a ticker has a registered contract in the broker.
    fn has_contract(&self, _ticker_id: &TickerId) -> bool {
        false
    }

    /// P21: Dynamically register a contract from watchlist metadata.
    /// Returns true if newly registered, false if already exists.
    fn register_dynamic_contract(
        &mut self,
        _ticker_id: TickerId,
        _symbol: &str,
        _exchange: &str,
        _currency: &str,
    ) -> bool {
        false
    }

    /// P21-FX: Get the trading currency for a ticker (ISO 4217 code).
    /// Used by the engine to convert native-currency prices to GBP.
    /// Default: "GBP" (no conversion needed for LSE-only brokers).
    fn currency_for_ticker(&self, _ticker_id: &TickerId) -> &str {
        "GBP"
    }

    /// P21-FX: Get the exchange for a ticker.
    /// Used by the engine for ISA gate checks with correct exchange MIC.
    fn exchange_for_ticker(&self, _ticker_id: &TickerId) -> &str {
        "XLON"
    }

    /// Get the human-readable symbol for a ticker ID.
    /// Used for logging and simulated trade reporting.
    fn symbol_for(&self, _ticker_id: TickerId) -> Option<String> {
        None
    }
}

/// Token bucket rate limiter (H16). Refills at `rate_per_sec` tokens/second.
pub struct TokenBucket {
    capacity: u32,
    tokens: f64,
    last_refill_ns: u64,
    rate_per_sec: f64,
}

impl TokenBucket {
    pub fn new(msgs_per_sec: u32) -> Self {
        Self {
            capacity: msgs_per_sec,
            tokens: msgs_per_sec as f64,
            last_refill_ns: 0,
            rate_per_sec: msgs_per_sec as f64,
        }
    }

    /// Try to consume one token. Returns true if allowed.
    pub fn try_consume(&mut self, now_ns: u64) -> bool {
        self.refill(now_ns);
        if self.tokens >= 1.0 {
            self.tokens -= 1.0;
            true
        } else {
            false
        }
    }

    /// SC-19: NTP-safe refill using saturating_sub to handle clock jumps backward.
    fn refill(&mut self, now_ns: u64) {
        let elapsed_ns = now_ns.saturating_sub(self.last_refill_ns);
        if elapsed_ns == 0 {
            return;
        }
        let elapsed_secs = elapsed_ns as f64 / 1_000_000_000.0;
        self.tokens = (self.tokens + elapsed_secs * self.rate_per_sec).min(self.capacity as f64);
        self.last_refill_ns = now_ns;
    }
}

/// SC-04: Two-tier data architecture.
/// Tier 1 (IBKR): Real-time 5-second bars, rate-limited by TokenBucket (60 req/10min).
/// Tier 2 (Polygon): Nightly batch via Python ouroboros, separate rate budget.
/// Python subprocess has its own TokenBucket instance to prevent cross-contamination.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DataTier {
    /// Real-time IBKR data (primary, rate-limited per IBKR rules).
    IbkrRealtime,
    /// Nightly Polygon.io batch data (secondary, separate rate budget).
    PolygonNightly,
}

/// Exponential backoff for broker reconnection (H17).
pub struct BackoffState {
    attempt: u32,
    base_ms: u64,
    max_ms: u64,
}

impl BackoffState {
    pub fn new(base_ms: u64, max_ms: u64) -> Self {
        Self {
            attempt: 0,
            base_ms,
            max_ms,
        }
    }

    /// Get the next backoff delay in milliseconds and increment attempt counter.
    pub fn next_delay_ms(&mut self) -> u64 {
        let delay = self.base_ms.saturating_mul(1u64 << self.attempt.min(10));
        self.attempt += 1;
        delay.min(self.max_ms)
    }

    /// Reset after successful connection.
    pub fn reset(&mut self) {
        self.attempt = 0;
    }

    pub fn attempt(&self) -> u32 {
        self.attempt
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_token_bucket_allows_within_limit() {
        let mut tb = TokenBucket::new(50);
        for _ in 0..50 {
            assert!(tb.try_consume(1_000_000_000));
        }
        // 51st should fail (no refill yet)
        assert!(!tb.try_consume(1_000_000_000));
    }

    #[test]
    fn test_token_bucket_refills() {
        let mut tb = TokenBucket::new(50);
        // Exhaust all tokens
        for _ in 0..50 {
            assert!(tb.try_consume(1_000_000_000));
        }
        assert!(!tb.try_consume(1_000_000_000));
        // After 1 second, should have 50 new tokens
        assert!(tb.try_consume(2_000_000_000));
    }

    #[test]
    fn test_backoff_exponential() {
        let mut bs = BackoffState::new(1000, 30_000);
        assert_eq!(bs.next_delay_ms(), 1000); // 1s
        assert_eq!(bs.next_delay_ms(), 2000); // 2s
        assert_eq!(bs.next_delay_ms(), 4000); // 4s
        assert_eq!(bs.next_delay_ms(), 8000); // 8s
        assert_eq!(bs.next_delay_ms(), 16000); // 16s
        assert_eq!(bs.next_delay_ms(), 30000); // capped at 30s
    }

    #[test]
    fn test_backoff_reset() {
        let mut bs = BackoffState::new(1000, 30_000);
        bs.next_delay_ms();
        bs.next_delay_ms();
        assert_eq!(bs.attempt(), 2);
        bs.reset();
        assert_eq!(bs.attempt(), 0);
        assert_eq!(bs.next_delay_ms(), 1000);
    }
}
