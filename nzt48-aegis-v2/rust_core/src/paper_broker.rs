//! PaperBroker — simulated broker for testing and paper trading.
//! Implements BrokerAdapter with configurable latency, partial fills, phantom fills.

use std::collections::{HashMap, HashSet, VecDeque};

use crate::broker::{
    BrokerAdapter, BrokerError, BrokerEvent, BrokerOpenOrder, BrokerPosition, TokenBucket,
};
use crate::types::{BrokerAckStatus, OrderSide, TickerId};

/// Configuration for PaperBroker simulation.
#[derive(Clone, Debug)]
pub struct PaperBrokerConfig {
    /// IBKR client ID. Executioner=100, Ouroboros=200 (H41).
    pub client_id: u32,
    /// Minimum simulated latency in milliseconds.
    pub latency_min_ms: u64,
    /// Maximum simulated latency in milliseconds.
    pub latency_max_ms: u64,
    /// Enable automatic partial fill splitting.
    pub partial_fill_enabled: bool,
    /// Number of chunks for partial fills.
    pub partial_fill_chunks: u32,
    /// Rate limit messages per second (H16).
    pub rate_limit_per_sec: u32,
    /// Heartbeat timeout in nanoseconds (60s default).
    pub heartbeat_timeout_ns: u64,
    /// Starting nextValidId from broker.
    pub initial_next_valid_id: u64,
    /// P3: Slippage model — percentage adverse fill adjustment.
    /// Buy fills at limit * (1 + slippage_pct/100), sells at limit * (1 - slippage_pct/100).
    pub slippage_pct: f64,
}

impl Default for PaperBrokerConfig {
    fn default() -> Self {
        Self {
            client_id: 100,
            latency_min_ms: 50,
            latency_max_ms: 200,
            partial_fill_enabled: false,
            partial_fill_chunks: 2,
            rate_limit_per_sec: 50,
            heartbeat_timeout_ns: 60_000_000_000,
            initial_next_valid_id: 1,
            slippage_pct: 0.5, // P3: Default 0.5% slippage (matches config.toml risk.slippage_assumption_pct)
        }
    }
}

/// Internal tracking of a submitted order.
#[derive(Debug)]
struct PendingOrder {
    order_id: String,
    ticker_id: TickerId,
    side: OrderSide,
    total_qty: u32,
    filled_qty: u32,
    limit_price: f64,
    ibkr_order_id: i64,
    pending_cancel: bool,
    cancelled: bool,
}

/// PaperBroker — simulated IBKR broker for testing and paper trading.
pub struct PaperBroker {
    config: PaperBrokerConfig,
    connected: bool,
    next_valid_id: u64,
    pending_orders: HashMap<String, PendingOrder>,
    filled_positions: HashMap<TickerId, BrokerPosition>,
    submitted_ids: HashSet<String>,
    events: VecDeque<BrokerEvent>,
    rate_limiter: TokenBucket,
    last_heartbeat_ns: u64,
    now_ns: u64,
    /// Exchange per ticker — loaded from contracts.toml for lot sizing realism.
    contract_exchanges: HashMap<TickerId, String>,
}

impl PaperBroker {
    pub fn new(config: PaperBrokerConfig) -> Self {
        let rate_limit = config.rate_limit_per_sec;
        let next_valid = config.initial_next_valid_id;
        Self {
            config,
            connected: true,
            next_valid_id: next_valid,
            pending_orders: HashMap::new(),
            filled_positions: HashMap::new(),
            submitted_ids: HashSet::new(),
            events: VecDeque::new(),
            rate_limiter: TokenBucket::new(rate_limit),
            last_heartbeat_ns: 0,
            now_ns: 0,
            contract_exchanges: HashMap::new(),
        }
    }

    /// Load exchange mappings from contract entries for lot sizing realism.
    pub fn load_contract_exchanges(&mut self, contracts: &[(TickerId, String)]) {
        for (tid, exchange) in contracts {
            self.contract_exchanges.insert(*tid, exchange.clone());
        }
    }

    /// Set the simulated current time.
    pub fn set_time_ns(&mut self, ns: u64) {
        self.now_ns = ns;
    }

    /// Simulate connection loss.
    pub fn disconnect(&mut self) {
        self.connected = false;
        self.events.push_back(BrokerEvent::Disconnected);
    }

    /// Simulate reconnection.
    pub fn reconnect(&mut self) {
        self.connected = true;
        self.last_heartbeat_ns = self.now_ns;
        self.events.push_back(BrokerEvent::Connected {
            next_valid_id: self.next_valid_id,
        });
    }

    /// Generate all remaining fills for an order (auto-split into chunks).
    pub fn generate_fills(&mut self, order_id: &str) -> Result<(), BrokerError> {
        let order = self
            .pending_orders
            .get(order_id)
            .ok_or_else(|| BrokerError::OrderNotFound(order_id.to_string()))?;
        if order.cancelled {
            return Ok(());
        }
        let remaining = order.total_qty - order.filled_qty;
        if remaining == 0 {
            return Ok(());
        }
        // P3: Apply slippage model — adverse fill adjustment per Book 12.
        // EXECUTION: TWAP-aware slippage — larger orders get more slippage (market impact).
        let notional = order.limit_price * remaining as f64;
        let base_slip = self.config.slippage_pct / 100.0;
        // Orders > £500: +0.1% per £500 bracket (simulates market impact)
        let impact_slip = if notional > 500.0 { (notional / 500.0).min(5.0) * 0.001 } else { 0.0 };
        let slip = base_slip + impact_slip;
        let price = match order.side {
            OrderSide::Buy => order.limit_price * (1.0 + slip),
            OrderSide::Sell => order.limit_price * (1.0 - slip),
        };
        let ticker_id = order.ticker_id;
        let chunks = if self.config.partial_fill_enabled {
            self.config.partial_fill_chunks.max(1)
        } else {
            1
        };
        let chunk_sizes = split_qty(remaining, chunks);
        let mut running_filled = order.filled_qty;
        let order_id_str = order.order_id.clone();
        for (_i, chunk_qty) in chunk_sizes.iter().enumerate() {
            running_filled += chunk_qty;
            let remaining_after = order.total_qty - running_filled;
            let exec_id = uuid::Uuid::now_v7().to_string();
            // IBKR ISA fee model:
            // - UK stocks (LSE): 0.05% of trade value, min £3.00
            // - US stocks: $0.005/share, min $1.00 (≈ £0.75)
            // - European: 0.05% of trade value, min €3.00
            let trade_value = price * (*chunk_qty as f64);
            let exchange = self.exchange_for_ticker(&ticker_id);
            let commission = match exchange {
                "LSE" | "LSEETF" => (trade_value * 0.0005).max(3.00),
                "SMART" | "XNYS" | "XNAS" | "ARCA" => {
                    (0.005 * (*chunk_qty as f64)).max(1.00).min(trade_value * 0.01) * 0.75 // USD→GBP approx
                }
                "IBIS" | "XETRA" => (trade_value * 0.0005).max(3.00),
                _ => (trade_value * 0.0005).max(3.00),
            };
            self.events.push_back(BrokerEvent::Fill {
                order_id: order_id_str.clone(),
                ticker_id,
                filled_qty: *chunk_qty,
                remaining_qty: remaining_after,
                price,
                exec_id,
                commission,
            });
        }
        let order = self
            .pending_orders
            .get_mut(&order_id_str)
            .expect("checked above");
        order.filled_qty = running_filled;
        match order.side {
            OrderSide::Buy => {
                let pos = self
                    .filled_positions
                    .entry(ticker_id)
                    .or_insert(BrokerPosition {
                        ticker_id,
                        qty: 0,
                        avg_cost: 0.0,
                    });
                pos.qty = running_filled;
                pos.avg_cost = price;
            }
            OrderSide::Sell => {
                // Sell fills reduce the position
                if let Some(pos) = self.filled_positions.get_mut(&ticker_id) {
                    pos.qty = pos.qty.saturating_sub(running_filled);
                    if pos.qty == 0 {
                        self.filled_positions.remove(&ticker_id);
                    }
                }
            }
        }
        Ok(())
    }

    /// Generate a single partial fill with specific qty and price.
    pub fn generate_fill(
        &mut self,
        order_id: &str,
        qty: u32,
        price: f64,
    ) -> Result<(), BrokerError> {
        let order = self
            .pending_orders
            .get_mut(order_id)
            .ok_or_else(|| BrokerError::OrderNotFound(order_id.to_string()))?;
        if order.cancelled {
            return Err(BrokerError::InvalidOrder("order cancelled".into()));
        }
        // P3: Apply slippage to single fills too (consistency with generate_fills).
        let slip = self.config.slippage_pct / 100.0;
        let price = match order.side {
            OrderSide::Buy => price * (1.0 + slip),
            OrderSide::Sell => price * (1.0 - slip),
        };
        let new_filled = order.filled_qty + qty;
        if new_filled > order.total_qty {
            return Err(BrokerError::InvalidOrder("overfill".into()));
        }
        let remaining = order.total_qty - new_filled;
        let exec_id = uuid::Uuid::now_v7().to_string();
        let ticker_id = order.ticker_id;
        let side = order.side;
        order.filled_qty = new_filled;
        self.events.push_back(BrokerEvent::Fill {
            order_id: order_id.to_string(),
            ticker_id,
            filled_qty: qty,
            remaining_qty: remaining,
            price,
            exec_id,
            commission: 1.50,
        });
        match side {
            OrderSide::Buy => {
                let pos = self
                    .filled_positions
                    .entry(ticker_id)
                    .or_insert(BrokerPosition {
                        ticker_id,
                        qty: 0,
                        avg_cost: 0.0,
                    });
                pos.qty = new_filled;
                pos.avg_cost = price;
            }
            OrderSide::Sell => {
                if let Some(pos) = self.filled_positions.get_mut(&ticker_id) {
                    pos.qty = pos.qty.saturating_sub(qty);
                    if pos.qty == 0 {
                        self.filled_positions.remove(&ticker_id);
                    }
                }
            }
        }
        Ok(())
    }

    /// Inject a phantom fill for a cancelled order (H55).
    /// Simulates: fill crossed in network before cancel arrived.
    pub fn inject_phantom_fill(
        &mut self,
        order_id: &str,
        qty: u32,
        price: f64,
    ) -> Result<(), BrokerError> {
        let order = self
            .pending_orders
            .get(order_id)
            .ok_or_else(|| BrokerError::OrderNotFound(order_id.to_string()))?;
        let ticker_id = order.ticker_id;
        let remaining = order.total_qty.saturating_sub(order.filled_qty + qty);
        let exec_id = uuid::Uuid::now_v7().to_string();
        self.events.push_back(BrokerEvent::Fill {
            order_id: order_id.to_string(),
            ticker_id,
            filled_qty: qty,
            remaining_qty: remaining,
            price,
            exec_id,
            commission: 1.50,
        });
        let pos = self
            .filled_positions
            .entry(ticker_id)
            .or_insert(BrokerPosition {
                ticker_id,
                qty: 0,
                avg_cost: 0.0,
            });
        pos.qty += qty;
        pos.avg_cost = price;
        Ok(())
    }

    /// Inject a broker-side position for reconciliation testing.
    /// Adds directly to filled_positions without going through order flow.
    pub fn inject_position(&mut self, position: BrokerPosition) {
        self.filled_positions.insert(position.ticker_id, position);
    }

    /// Inject a broker-side open order for orphan detection testing.
    pub fn inject_open_order(&mut self, order_id: &str, ticker_id: TickerId, qty: u32) {
        let ibkr_order_id = self.next_valid_id as i64;
        self.next_valid_id += 1;
        self.pending_orders.insert(
            order_id.to_string(),
            PendingOrder {
                order_id: order_id.to_string(),
                ticker_id,
                side: OrderSide::Buy,
                total_qty: qty,
                filled_qty: 0,
                limit_price: 0.0,
                ibkr_order_id,
                pending_cancel: false,
                cancelled: false,
            },
        );
    }
}

impl BrokerAdapter for PaperBroker {
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
        let ibkr_order_id = self.next_valid_id as i64;
        self.next_valid_id += 1;
        self.submitted_ids.insert(order_id.to_string());
        self.pending_orders.insert(
            order_id.to_string(),
            PendingOrder {
                order_id: order_id.to_string(),
                ticker_id,
                side,
                total_qty: qty,
                filled_qty: 0,
                limit_price,
                ibkr_order_id,
                pending_cancel: false,
                cancelled: false,
            },
        );
        self.events.push_back(BrokerEvent::Ack {
            order_id: order_id.to_string(),
            ibkr_order_id,
            status: BrokerAckStatus::Accepted,
            message: None,
        });
        Ok(())
    }

    fn cancel_order(&mut self, order_id: &str) -> Result<(), BrokerError> {
        if !self.connected {
            return Err(BrokerError::NotConnected);
        }
        let order = self
            .pending_orders
            .get_mut(order_id)
            .ok_or_else(|| BrokerError::OrderNotFound(order_id.to_string()))?;
        if order.cancelled {
            return Err(BrokerError::InvalidOrder("already cancelled".into()));
        }
        order.pending_cancel = true;
        let ibkr_id = order.ibkr_order_id;
        self.events.push_back(BrokerEvent::Ack {
            order_id: order_id.to_string(),
            ibkr_order_id: ibkr_id,
            status: BrokerAckStatus::PendingCancel,
            message: None,
        });
        order.cancelled = true;
        self.events.push_back(BrokerEvent::Ack {
            order_id: order_id.to_string(),
            ibkr_order_id: ibkr_id,
            status: BrokerAckStatus::Cancelled,
            message: None,
        });
        Ok(())
    }

    fn request_positions(&self) -> Result<Vec<BrokerPosition>, BrokerError> {
        if !self.connected {
            return Err(BrokerError::NotConnected);
        }
        Ok(self.filled_positions.values().cloned().collect())
    }

    fn request_open_orders(&self) -> Result<Vec<BrokerOpenOrder>, BrokerError> {
        if !self.connected {
            return Err(BrokerError::NotConnected);
        }
        let orders = self
            .pending_orders
            .values()
            .filter(|o| !o.cancelled && o.filled_qty < o.total_qty)
            .map(|o| BrokerOpenOrder {
                order_id: o.order_id.clone(),
                ibkr_order_id: o.ibkr_order_id,
                ticker_id: o.ticker_id,
                qty: o.total_qty - o.filled_qty,
                status: if o.pending_cancel {
                    "PendingCancel".to_string()
                } else {
                    "Submitted".to_string()
                },
            })
            .collect();
        Ok(orders)
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
        self.next_valid_id
    }

    fn client_id(&self) -> u32 {
        self.config.client_id
    }

    fn exchange_for_ticker(&self, ticker_id: &TickerId) -> &str {
        self.contract_exchanges
            .get(ticker_id)
            .map(|s| s.as_str())
            .unwrap_or("XLON")
    }
}

/// Split a quantity into N chunks (as evenly as possible).
fn split_qty(total: u32, chunks: u32) -> Vec<u32> {
    if chunks <= 1 || total <= 1 {
        return vec![total];
    }
    let base = total / chunks;
    let remainder = total % chunks;
    let mut result = Vec::with_capacity(chunks as usize);
    for i in 0..chunks {
        result.push(base + if i < remainder { 1 } else { 0 });
    }
    result
}
