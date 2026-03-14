//! IbkrBroker — real IBKR adapter via `ibapi` crate.
//! Implements BrokerAdapter trait + market data subscriptions.
//!
//! Connection: IB Gateway on localhost:4002 (paper) or 4001 (live).
//! Client ID: 101 (Executioner V2) per config.toml.

use std::collections::{HashMap, HashSet, VecDeque};

use ibapi::client::blocking::Client;
use ibapi::contracts::Contract;
use ibapi::market_data::realtime::{BarSize, BidAsk, WhatToShow};
use ibapi::prelude::TradingHours;
use ibapi::subscriptions::sync::Subscription;

use crate::broker::{
    BrokerAdapter, BrokerError, BrokerEvent, BrokerOpenOrder, BrokerPosition, TokenBucket,
};
use crate::types::{BrokerAckStatus, MarketTick, OrderSide, TickerId};

/// Configuration for the IBKR broker connection.
#[derive(Clone, Debug)]
pub struct IbkrBrokerConfig {
    pub host: String,
    pub port: u16,
    pub client_id: u32,
    pub rate_limit_per_sec: u32,
    pub heartbeat_timeout_ns: u64,
}

impl Default for IbkrBrokerConfig {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 4002,
            client_id: 101,
            rate_limit_per_sec: 50,
            heartbeat_timeout_ns: 60_000_000_000,
        }
    }
}

/// Symbol-to-TickerId mapping entry.
#[derive(Clone, Debug)]
pub struct ContractMapping {
    pub ticker_id: TickerId,
    pub symbol: String,
    pub ibkr_symbol: String,
    pub exchange: String,
    pub currency: String,
}

/// Real-time bar subscription handle.
struct BarSubscription {
    ticker_id: TickerId,
    #[allow(dead_code)]
    symbol: String,
    sub: Subscription<ibapi::market_data::realtime::Bar>,
}

/// Level 1 tick-by-tick bid/ask subscription handle.
struct L1Subscription {
    ticker_id: TickerId,
    #[allow(dead_code)]
    symbol: String,
    sub: Subscription<BidAsk>,
}

/// Real IBKR broker adapter via `ibapi` crate.
pub struct IbkrBroker {
    config: IbkrBrokerConfig,
    client: Option<Client>,
    connected: bool,
    next_valid_id: i32,
    order_id_map: HashMap<String, i32>,
    submitted_ids: HashSet<String>,
    events: VecDeque<BrokerEvent>,
    rate_limiter: TokenBucket,
    last_heartbeat_ns: u64,
    now_ns: u64,
    // Market data
    bar_subs: Vec<BarSubscription>,
    l1_subs: Vec<L1Subscription>,
    /// Real-time L1 bid/ask cache from tick-by-tick subscriptions (P0-01).
    l1_cache: HashMap<TickerId, (f64, f64)>,
    pending_ticks: VecDeque<MarketTick>,
    // Contract mappings
    contract_map: HashMap<TickerId, ContractMapping>,
    // Cached positions/orders from last request
    cached_positions: Vec<BrokerPosition>,
    cached_orders: Vec<BrokerOpenOrder>,
    /// Bar high/low data per ticker from last poll (for ATR calculation).
    pub bar_high_low: HashMap<TickerId, Vec<(f64, f64)>>,
    /// SC-19: Reconnection count for client_id rotation on Error 326.
    reconnect_count: u32,
}

impl IbkrBroker {
    pub fn new(config: IbkrBrokerConfig) -> Self {
        let rate_limit = config.rate_limit_per_sec;
        Self {
            config,
            client: None,
            connected: false,
            next_valid_id: 1,
            order_id_map: HashMap::new(),
            submitted_ids: HashSet::new(),
            events: VecDeque::new(),
            rate_limiter: TokenBucket::new(rate_limit),
            last_heartbeat_ns: 0,
            now_ns: 0,
            bar_subs: Vec::new(),
            l1_subs: Vec::new(),
            l1_cache: HashMap::new(),
            pending_ticks: VecDeque::new(),
            contract_map: HashMap::new(),
            cached_positions: Vec::new(),
            cached_orders: Vec::new(),
            bar_high_low: HashMap::new(),
            reconnect_count: 0,
        }
    }

    /// Set the current time (for rate limiter).
    pub fn set_time_ns(&mut self, ns: u64) {
        self.now_ns = ns;
    }

    /// Connect to IB Gateway via ibapi.
    /// SC-14: reqMarketDataType(1) = RealTime. Requires active market data subscription.
    /// Falls back to DelayedFrozen if RealTime fails (e.g. no subscription).
    pub fn connect(&mut self) -> Result<(), BrokerError> {
        let addr = format!("{}:{}", self.config.host, self.config.port);
        match Client::connect(&addr, self.config.client_id as i32) {
            Ok(client) => {
                // SC-14: Request real-time data (Type 1). Requires market data subscription.
                // Paper accounts share the live account's market data subscriptions.
                if let Err(e) = client.switch_market_data_type(ibapi::market_data::MarketDataType::Realtime) {
                    eprintln!("IBKR: switch_market_data_type(RealTime) failed: {e}, falling back to DelayedFrozen");
                    let _ = client.switch_market_data_type(ibapi::market_data::MarketDataType::DelayedFrozen);
                } else {
                    eprintln!("IBKR: reqMarketDataType(1) — real-time (SC-14)");
                }

                // Monotonic: never accept a lower ID (IBKR can re-send during farm flaps)
                self.next_valid_id = self.next_valid_id.max(client.next_order_id());
                self.client = Some(client);
                self.connected = true;
                self.last_heartbeat_ns = self.now_ns;
                self.events.push_back(BrokerEvent::Connected {
                    next_valid_id: self.next_valid_id as u64,
                });
                eprintln!(
                    "IBKR: Connected to {} (client_id={})",
                    addr, self.config.client_id
                );
                Ok(())
            }
            Err(e) => {
                eprintln!("IBKR: Connection failed to {addr}: {e}");
                Err(BrokerError::NotConnected)
            }
        }
    }

    /// Disconnect from IB Gateway.
    pub fn disconnect(&mut self) {
        self.bar_subs.clear();
        self.l1_subs.clear();
        self.l1_cache.clear();
        self.client = None;
        self.connected = false;
        self.events.push_back(BrokerEvent::Disconnected);
    }

    /// SC-19: Rotate client_id on Error 326 ("Cannot connect to TWS").
    /// Uses pattern: base_id + (reconnect_count % 5) to cycle through 101-105.
    pub fn rotate_client_id(&mut self) {
        self.reconnect_count += 1;
        let base_id = self.config.client_id;
        let rotated = base_id + (self.reconnect_count % 5);
        eprintln!(
            "IBKR: client_id rotation {} → {} (reconnect #{})",
            self.config.client_id, rotated, self.reconnect_count
        );
        self.config.client_id = rotated;
    }

    /// SC-19: Get current reconnect count.
    pub fn reconnect_count(&self) -> u32 {
        self.reconnect_count
    }

    /// Register a contract mapping (symbol → TickerId).
    pub fn register_contract(&mut self, mapping: ContractMapping) {
        self.contract_map.insert(mapping.ticker_id, mapping);
    }

    /// Build an ibapi Contract from our config.
    fn build_contract(mapping: &ContractMapping) -> Contract {
        Contract::stock(&mapping.ibkr_symbol)
            .on_exchange(&mapping.exchange)
            .in_currency(&mapping.currency)
            .primary(&mapping.exchange)
            .build()
    }

    /// Subscribe to 5-second realtime bars for a contract.
    pub fn subscribe_bars(&mut self, ticker_id: TickerId) -> Result<(), BrokerError> {
        let client = self.client.as_ref().ok_or(BrokerError::NotConnected)?;
        let mapping = self
            .contract_map
            .get(&ticker_id)
            .ok_or_else(|| {
                BrokerError::InvalidOrder(format!("No contract for ticker {}", ticker_id.0))
            })?
            .clone();

        let contract = Self::build_contract(&mapping);
        match client.realtime_bars(
            &contract,
            BarSize::Sec5,
            WhatToShow::Trades,
            TradingHours::Regular,
        ) {
            Ok(sub) => {
                eprintln!(
                    "IBKR: Subscribed to bars for {} (ticker_id={})",
                    mapping.symbol, ticker_id.0
                );
                self.bar_subs.push(BarSubscription {
                    ticker_id,
                    symbol: mapping.symbol,
                    sub,
                });
                Ok(())
            }
            Err(e) => {
                eprintln!("IBKR: Bar subscription failed for {}: {e}", mapping.symbol);
                Err(BrokerError::InvalidOrder(format!("subscribe: {e}")))
            }
        }
    }

    /// Subscribe to bars for all registered contracts.
    pub fn subscribe_all(&mut self) -> u32 {
        let ticker_ids: Vec<TickerId> = self.contract_map.keys().copied().collect();
        let mut count = 0u32;
        for tid in ticker_ids {
            if self.subscribe_bars(tid).is_ok() {
                count += 1;
            }
        }
        count
    }

    /// Subscribe to L1 tick-by-tick bid/ask for a contract (P0-01).
    /// Provides real bid/ask quotes instead of synthetic spread estimation.
    pub fn subscribe_l1(&mut self, ticker_id: TickerId) -> Result<(), BrokerError> {
        let client = self.client.as_ref().ok_or(BrokerError::NotConnected)?;
        let mapping = self
            .contract_map
            .get(&ticker_id)
            .ok_or_else(|| {
                BrokerError::InvalidOrder(format!("No contract for ticker {}", ticker_id.0))
            })?
            .clone();

        let contract = Self::build_contract(&mapping);
        match client.tick_by_tick_bid_ask(&contract, 0, true) {
            Ok(sub) => {
                eprintln!(
                    "IBKR: L1 bid/ask subscribed for {} (ticker_id={})",
                    mapping.symbol, ticker_id.0
                );
                self.l1_subs.push(L1Subscription {
                    ticker_id,
                    symbol: mapping.symbol,
                    sub,
                });
                Ok(())
            }
            Err(e) => {
                eprintln!("IBKR: L1 subscription failed for {}: {e}", mapping.symbol);
                Err(BrokerError::InvalidOrder(format!("L1 subscribe: {e}")))
            }
        }
    }

    /// Subscribe to L1 bid/ask for all registered contracts.
    pub fn subscribe_all_l1(&mut self) -> u32 {
        let ticker_ids: Vec<TickerId> = self.contract_map.keys().copied().collect();
        let mut count = 0u32;
        for tid in ticker_ids {
            if self.subscribe_l1(tid).is_ok() {
                count += 1;
            }
        }
        count
    }

    /// Unsubscribe from L1 bid/ask for a specific ticker (P21: Mode rotation).
    pub fn unsubscribe_l1(&mut self, ticker_id: TickerId) -> Result<(), BrokerError> {
        // Find and remove the L1 subscription
        if let Some(pos) = self.l1_subs.iter().position(|sub| sub.ticker_id == ticker_id) {
            self.l1_subs.remove(pos);
            // Also remove from cache
            self.l1_cache.remove(&ticker_id);
            eprintln!(
                "IBKR: Unsubscribed L1 for ticker_id={} (count: {})",
                ticker_id.0,
                self.l1_subs.len()
            );
            Ok(())
        } else {
            eprintln!(
                "IBKR: L1 unsubscribe failed — ticker_id={} not subscribed",
                ticker_id.0
            );
            Err(BrokerError::InvalidOrder(format!(
                "L1 subscription not found for {}",
                ticker_id.0
            )))
        }
    }

    /// Batch unsubscribe from L1 for a set of tickers (P21: Mode rotation).
    pub fn unsubscribe_l1_batch(&mut self, ticker_ids: &[TickerId]) -> u32 {
        let mut unsubscribed = 0u32;
        for &tid in ticker_ids {
            if self.unsubscribe_l1(tid).is_ok() {
                unsubscribed += 1;
            }
        }
        eprintln!(
            "IBKR: Batch unsubscribed {} tickers from L1 (remaining: {})",
            unsubscribed,
            self.l1_subs.len()
        );
        unsubscribed
    }

    /// Batch subscribe to L1 for a set of tickers (P21: Mode rotation).
    pub fn subscribe_l1_batch(&mut self, ticker_ids: &[TickerId]) -> u32 {
        let mut subscribed = 0u32;
        for &tid in ticker_ids {
            if self.subscribe_l1(tid).is_ok() {
                subscribed += 1;
            }
        }
        eprintln!(
            "IBKR: Batch subscribed {} tickers to L1 (total: {})",
            subscribed,
            self.l1_subs.len()
        );
        subscribed
    }

    /// Get current count of active L1 subscriptions (P21: telemetry).
    pub fn l1_subscription_count(&self) -> u32 {
        self.l1_subs.len() as u32
    }

    /// Poll all bar subscriptions and L1 tick-by-tick subscriptions. Non-blocking.
    pub fn poll_ticks(&mut self) {
        // 1. Drain L1 bid/ask ticks into cache (P0-01).
        for l1_sub in &self.l1_subs {
            while let Some(ba) = l1_sub.sub.try_next() {
                if ba.bid_price > 0.0 && ba.ask_price > 0.0 {
                    self.l1_cache.insert(l1_sub.ticker_id, (ba.bid_price, ba.ask_price));
                }
            }
        }

        // 2. Drain bar subscriptions, using L1 cache for bid/ask when available.
        for bar_sub in &self.bar_subs {
            while let Some(bar) = bar_sub.sub.try_next() {
                let ts_ns = bar.date.unix_timestamp_nanos() as u64;
                // P0-01: Use real L1 bid/ask from cache. Fall back to synthetic
                // spread estimation only during cold start (before L1 data arrives).
                let (bid, ask) = if let Some(&(b, a)) = self.l1_cache.get(&bar_sub.ticker_id) {
                    (b, a)
                } else {
                    let spread_est = (bar.high - bar.low).max(0.0) * 0.1;
                    (
                        bar.close - spread_est.max(bar.close * 0.0001),
                        bar.close + spread_est.max(bar.close * 0.0001),
                    )
                };
                let tick = MarketTick {
                    ticker_id: bar_sub.ticker_id,
                    bid,
                    ask,
                    last: bar.close,
                    volume: bar.volume as u64,
                    timestamp_ns: ts_ns,
                    recv_timestamp_ns: self.now_ns,
                };
                self.pending_ticks.push_back(tick);
                // Store bar high/low for ATR calculation
                self.bar_high_low
                    .entry(bar_sub.ticker_id)
                    .or_default()
                    .push((bar.high, bar.low));
            }
        }
    }

    /// Drain pending market ticks.
    pub fn drain_ticks(&mut self) -> Vec<MarketTick> {
        self.pending_ticks.drain(..).collect()
    }

    /// Poll broker events (order updates, errors). Non-blocking.
    pub fn poll_events(&mut self) {
        if !self.connected {
            return;
        }
        // Check if client is still connected
        if let Some(ref client) = self.client
            && !client.is_connected()
        {
            self.connected = false;
            self.events.push_back(BrokerEvent::Disconnected);
        }
    }

    /// Inject a fill event (for testing).
    pub fn inject_fill(&mut self, order_id: &str, ticker_id: TickerId, qty: u32, price: f64) {
        let exec_id = uuid::Uuid::now_v7().to_string();
        self.events.push_back(BrokerEvent::Fill {
            order_id: order_id.to_string(),
            ticker_id,
            filled_qty: qty,
            remaining_qty: 0,
            price,
            exec_id,
            commission: 1.50,
        });
    }

    /// Inject a position for reconciliation.
    pub fn inject_position(&mut self, pos: BrokerPosition) {
        self.cached_positions.push(pos);
    }
}

impl BrokerAdapter for IbkrBroker {
    fn submit_order(
        &mut self,
        order_id: &str,
        ticker_id: TickerId,
        side: OrderSide,
        qty: u32,
        limit_price: f64,
    ) -> Result<(), BrokerError> {
        if !self.connected {
            return Err(BrokerError::NotConnected);
        }
        if self.submitted_ids.contains(order_id) {
            return Err(BrokerError::DuplicateOrderId(order_id.to_string()));
        }
        if !self.rate_limiter.try_consume(self.now_ns) {
            return Err(BrokerError::RateLimitExceeded);
        }

        let client = self.client.as_ref().ok_or(BrokerError::NotConnected)?;
        let mapping = self.contract_map.get(&ticker_id).ok_or_else(|| {
            BrokerError::InvalidOrder(format!("No contract for ticker {}", ticker_id.0))
        })?;

        let contract = Self::build_contract(mapping);
        let order_builder = client.order(&contract);
        let ibkr_order_id = match side {
            OrderSide::Buy => match order_builder.buy(qty as f64).limit(limit_price).submit() {
                Ok(id) => id,
                Err(e) => {
                    return Err(BrokerError::InvalidOrder(format!("submit buy: {e}")));
                }
            },
            OrderSide::Sell => match order_builder.sell(qty as f64).limit(limit_price).submit() {
                Ok(id) => id,
                Err(e) => {
                    return Err(BrokerError::InvalidOrder(format!("submit sell: {e}")));
                }
            },
        };

        let ibkr_id_i32: i32 = ibkr_order_id.into();
        self.submitted_ids.insert(order_id.to_string());
        self.order_id_map.insert(order_id.to_string(), ibkr_id_i32);

        eprintln!(
            "IBKR: Submitted {side} order {order_id} → ibkr_id={ibkr_id_i32}, \
             ticker={}, qty={qty}, limit={limit_price:.4}",
            ticker_id.0
        );

        self.events.push_back(BrokerEvent::Ack {
            order_id: order_id.to_string(),
            ibkr_order_id: ibkr_id_i32 as i64,
            status: BrokerAckStatus::Accepted,
            message: None,
        });

        Ok(())
    }

    fn cancel_order(&mut self, order_id: &str) -> Result<(), BrokerError> {
        if !self.connected {
            return Err(BrokerError::NotConnected);
        }
        let ibkr_id = *self
            .order_id_map
            .get(order_id)
            .ok_or_else(|| BrokerError::OrderNotFound(order_id.to_string()))?;

        if let Some(ref client) = self.client {
            let _ = client.cancel_order(ibkr_id, "");
        }

        self.events.push_back(BrokerEvent::Ack {
            order_id: order_id.to_string(),
            ibkr_order_id: ibkr_id as i64,
            status: BrokerAckStatus::Cancelled,
            message: None,
        });
        Ok(())
    }

    fn request_positions(&self) -> Result<Vec<BrokerPosition>, BrokerError> {
        if !self.connected {
            return Err(BrokerError::NotConnected);
        }
        Ok(self.cached_positions.clone())
    }

    fn request_open_orders(&self) -> Result<Vec<BrokerOpenOrder>, BrokerError> {
        if !self.connected {
            return Err(BrokerError::NotConnected);
        }
        Ok(self.cached_orders.clone())
    }

    fn heartbeat(&mut self) -> Result<(), BrokerError> {
        self.last_heartbeat_ns = self.now_ns;
        Ok(())
    }

    fn is_connected(&self) -> bool {
        if !self.connected {
            return false;
        }
        if self.last_heartbeat_ns > 0
            && self.now_ns > self.last_heartbeat_ns + self.config.heartbeat_timeout_ns
        {
            return false;
        }
        true
    }

    fn drain_events(&mut self) -> Vec<BrokerEvent> {
        self.events.drain(..).collect()
    }

    fn next_valid_id(&self) -> u64 {
        self.next_valid_id as u64
    }

    fn client_id(&self) -> u32 {
        self.config.client_id
    }

    /// P21: Batch subscribe to L1 for a set of tickers (mode rotation).
    fn subscribe_l1_batch(&mut self, ticker_ids: &[TickerId]) -> u32 {
        let mut subscribed = 0u32;
        for &tid in ticker_ids {
            if self.subscribe_l1(tid).is_ok() {
                subscribed += 1;
            }
        }
        eprintln!(
            "IBKR: Batch subscribed {} tickers to L1 (total: {})",
            subscribed,
            self.l1_subs.len()
        );
        subscribed
    }

    /// P21: Batch unsubscribe from L1 for a set of tickers (mode rotation).
    fn unsubscribe_l1_batch(&mut self, ticker_ids: &[TickerId]) -> u32 {
        let mut unsubscribed = 0u32;
        for &tid in ticker_ids {
            if self.unsubscribe_l1(tid).is_ok() {
                unsubscribed += 1;
            }
        }
        eprintln!(
            "IBKR: Batch unsubscribed {} tickers from L1 (remaining: {})",
            unsubscribed,
            self.l1_subs.len()
        );
        unsubscribed
    }

    /// P21: Get current count of active L1 subscriptions (telemetry).
    fn l1_subscription_count(&self) -> u32 {
        self.l1_subs.len() as u32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ibkr_broker_new() {
        let broker = IbkrBroker::new(IbkrBrokerConfig::default());
        assert!(!broker.is_connected());
        assert_eq!(broker.client_id(), 101);
        assert_eq!(broker.next_valid_id(), 1);
    }

    #[test]
    fn test_submit_without_connect_fails() {
        let mut broker = IbkrBroker::new(IbkrBrokerConfig::default());
        let result = broker.submit_order("test-1", TickerId(1), OrderSide::Buy, 100, 10.0);
        assert!(matches!(result, Err(BrokerError::NotConnected)));
    }

    #[test]
    fn test_config_default_paper_port() {
        let config = IbkrBrokerConfig::default();
        assert_eq!(config.port, 4002);
        assert_eq!(config.client_id, 101);
        assert_eq!(config.rate_limit_per_sec, 50);
    }

    #[test]
    fn test_contract_mapping_registration() {
        let mut broker = IbkrBroker::new(IbkrBrokerConfig::default());
        broker.register_contract(ContractMapping {
            ticker_id: TickerId(0),
            symbol: "QQQ3.L".to_string(),
            ibkr_symbol: "QQQ3".to_string(),
            exchange: "LSE".to_string(),
            currency: "GBP".to_string(),
        });
        assert!(broker.contract_map.contains_key(&TickerId(0)));
    }

    // ── P0-01: L1 cache provides real bid/ask ──
    #[test]
    fn test_l1_cache_stores_bid_ask() {
        let mut broker = IbkrBroker::new(IbkrBrokerConfig::default());
        // Simulate L1 data arriving
        broker.l1_cache.insert(TickerId(0), (10.45, 10.50));
        broker.l1_cache.insert(TickerId(1), (25.00, 25.05));

        let (bid, ask) = broker.l1_cache.get(&TickerId(0)).copied().unwrap_or((0.0, 0.0));
        assert!((bid - 10.45).abs() < 1e-10);
        assert!((ask - 10.50).abs() < 1e-10);

        let (bid, ask) = broker.l1_cache.get(&TickerId(1)).copied().unwrap_or((0.0, 0.0));
        assert!((bid - 25.00).abs() < 1e-10);
        assert!((ask - 25.05).abs() < 1e-10);

        // Missing ticker falls back
        let (bid, ask) = broker.l1_cache.get(&TickerId(99)).copied().unwrap_or((0.0, 0.0));
        assert!((bid - 0.0).abs() < 1e-10);
        assert!((ask - 0.0).abs() < 1e-10);
    }

    #[test]
    fn test_l1_cache_cleared_on_disconnect() {
        let mut broker = IbkrBroker::new(IbkrBrokerConfig::default());
        broker.l1_cache.insert(TickerId(0), (10.45, 10.50));
        assert!(!broker.l1_cache.is_empty());
        broker.disconnect();
        assert!(broker.l1_cache.is_empty());
    }
}
