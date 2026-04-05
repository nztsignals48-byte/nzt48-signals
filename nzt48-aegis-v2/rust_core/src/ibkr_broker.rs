//! IbkrBroker — real IBKR adapter via `ibapi` crate.
//! Implements BrokerAdapter trait + market data subscriptions.
//!
//! Connection: IB Gateway on localhost:4003 (gnzsnz paper proxy) or 4001 (live).
//! Client ID: 101 (Executioner V2) per config.toml.

use std::collections::{HashMap, HashSet, VecDeque};

use ibapi::client::blocking::Client;
use ibapi::contracts::Contract;
use ibapi::contracts::tick_types::TickType;
use ibapi::market_data::realtime::{BarSize, BidAsk, TickTypes, WhatToShow};
use ibapi::orders::OrderUpdate;
use ibapi::prelude::TradingHours;
use ibapi::subscriptions::sync::Subscription;

use crate::broker::{
    BrokerAdapter, BrokerError, BrokerEvent, BrokerOpenOrder, BrokerPosition, TokenBucket,
    min_lot_for_exchange,
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
            port: 4003, // gnzsnz/ib-gateway paper API proxy port
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

/// reqMktData streaming subscription handle (works with delayed data).
struct MktDataSubscription {
    ticker_id: TickerId,
    symbol: String,
    sub: Subscription<TickTypes>,
}

/// Streaming tick accumulator — builds synthetic bars from reqMktData ticks.
/// Throttled: only emits a MarketTick every 5 seconds to prevent backpressure.
struct TickAccumulator {
    bid: f64,
    ask: f64,
    last: f64,
    high: f64,
    low: f64,
    volume: u64,
    bid_size: i32,
    ask_size: i32,
    has_data: bool,
    /// Nanosecond timestamp of last emitted tick (throttle to 5s cadence).
    last_emit_ns: u64,
}

impl Default for TickAccumulator {
    fn default() -> Self {
        Self {
            bid: 0.0,
            ask: 0.0,
            last: 0.0,
            high: 0.0,
            low: 0.0,
            volume: 0,
            bid_size: 0,
            ask_size: 0,
            has_data: false,
            last_emit_ns: 0,
        }
    }
}

// P1-2.11: min_lot_for_exchange() moved to broker.rs as shared utility.

/// P1-2.12: Quantize price to exchange-appropriate tick size.
/// Prevents order rejects from sub-tick pricing. LSE and TSE use
/// variable tick tables; most other exchanges use 0.01.
fn quantize_price(price: f64, exchange: &str) -> f64 {
    let tick_size = match exchange {
        "LSEETF" | "LSE" | "XLON" => {
            // LSE variable tick table (pence/GBX or USD depending on instrument)
            if price < 0.5 { 0.0001 }
            else if price < 1.0 { 0.0005 }
            else if price < 5.0 { 0.001 }
            else if price < 10.0 { 0.005 }
            else { 0.01 }
        }
        "TSEJ" | "TSE" => {
            // TSE tick sizes (yen)
            if price < 3000.0 { 1.0 }
            else if price < 5000.0 { 5.0 }
            else if price < 30000.0 { 10.0 }
            else { 50.0 }
        }
        _ => 0.01, // Most exchanges use 0.01
    };
    (price / tick_size).round() * tick_size
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
    // Market data — dual track: reqRealTimeBars (primary) + reqMktData (fallback)
    bar_subs: Vec<BarSubscription>,
    l1_subs: Vec<L1Subscription>,
    /// reqMktData streaming subscriptions (works with delayed data).
    mktdata_subs: Vec<MktDataSubscription>,
    /// Tick accumulators per ticker (for building synthetic bars from reqMktData).
    tick_accum: HashMap<TickerId, TickAccumulator>,
    /// Tickers that have bar failures — use reqMktData for these.
    bar_failed_tickers: HashSet<TickerId>,
    /// Real-time L1 bid/ask cache from tick-by-tick subscriptions (P0-01).
    l1_cache: HashMap<TickerId, (f64, f64)>,
    pending_ticks: VecDeque<MarketTick>,
    /// Order update stream subscription for fill detection (ibapi).
    order_update_sub: Option<Subscription<OrderUpdate>>,
    // Contract mappings
    contract_map: HashMap<TickerId, ContractMapping>,
    // Cached positions/orders from last request
    cached_positions: Vec<BrokerPosition>,
    cached_orders: Vec<BrokerOpenOrder>,
    /// Bar high/low data per ticker from last poll (for ATR calculation).
    pub bar_high_low: HashMap<TickerId, Vec<(f64, f64)>>,
    /// SC-19: Reconnection count for client_id rotation on Error 326.
    reconnect_count: u32,
    /// Diagnostic: total polls with zero data (for detecting silent subscription failures).
    zero_data_polls: u32,
    /// Diagnostic: total bars ever received (lifetime counter).
    total_bars_received: u64,
    /// Diagnostic: total L1 ticks ever received (lifetime counter).
    total_l1_received: u64,
    /// Diagnostic: subscription errors detected (ibapi bug #434 — errors come as ParseInt).
    sub_errors_detected: u32,
    /// P1-2.18: Last cancel/modify time per order_id (prevent IBKR pacing violations).
    last_modify_ns: HashMap<String, u64>,
    /// Set of tickers with active L1 tick-by-tick subscriptions.
    /// Used to gate signal generation — only trade on continuous tape data.
    l1_subscribed_set: HashSet<TickerId>,
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
            mktdata_subs: Vec::new(),
            tick_accum: HashMap::new(),
            bar_failed_tickers: HashSet::new(),
            l1_cache: HashMap::new(),
            pending_ticks: VecDeque::new(),
            order_update_sub: None,
            contract_map: HashMap::new(),
            cached_positions: Vec::new(),
            cached_orders: Vec::new(),
            bar_high_low: HashMap::new(),
            reconnect_count: 0,
            zero_data_polls: 0,
            total_bars_received: 0,
            total_l1_received: 0,
            sub_errors_detected: 0,
            last_modify_ns: HashMap::new(),
            l1_subscribed_set: HashSet::new(),
        }
    }

    /// Set the current time (for rate limiter).
    pub fn set_time_ns(&mut self, ns: u64) {
        self.now_ns = ns;
    }

    /// Connect to IB Gateway via ibapi.
    /// SC-14: Try RealTime first, fall back to DelayedFrozen for paper accounts.
    /// Paper accounts need market data sharing enabled in Account Management.
    pub fn connect(&mut self) -> Result<(), BrokerError> {
        let addr = format!("{}:{}", self.config.host, self.config.port);
        match Client::connect(&addr, self.config.client_id as i32) {
            Ok(client) => {
                // SC-14: Request Delayed data (Type 3) — provides real-time for exchanges with
                // Type 3 (Delayed): real-time for subscribed exchanges (user has LSE L2),
                // 15-min delayed for unsubscribed. LSE ETPs get real-time data.
                // Type 1 would block data for unsubscribed exchanges entirely.
                match client.switch_market_data_type(ibapi::market_data::MarketDataType::Delayed) {
                    Ok(_) => eprintln!("IBKR: reqMarketDataType(3) — REAL-TIME for LSE (L2 sub), delayed for unsubscribed exchanges"),
                    Err(e) => eprintln!("IBKR: switch_market_data_type(3) failed: {e}"),
                }

                // Monotonic: never accept a lower ID (IBKR can re-send during farm flaps)
                self.next_valid_id = self.next_valid_id.max(client.next_order_id());

                // Subscribe to order update stream for fill detection.
                // This single subscription receives ALL order status, execution,
                // and commission updates across all orders.
                match client.order_update_stream() {
                    Ok(sub) => {
                        eprintln!("IBKR: order_update_stream subscribed (fill detection active)");
                        self.order_update_sub = Some(sub);
                    }
                    Err(e) => {
                        eprintln!("IBKR: order_update_stream failed: {e} (fills will NOT be detected)");
                    }
                }

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
        self.mktdata_subs.clear();
        self.tick_accum.clear();
        self.bar_failed_tickers.clear();
        self.l1_cache.clear();
        self.order_update_sub = None;
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

    /// Dynamically register a contract inferred from symbol and watchlist metadata.
    /// Used when watchlist introduces tickers not in contracts.toml.
    /// Returns true if newly registered, false if already exists.
    pub fn register_dynamic_contract(
        &mut self,
        ticker_id: TickerId,
        symbol: &str,
        exchange_hint: &str,
        currency_hint: &str,
    ) -> bool {
        if self.contract_map.contains_key(&ticker_id) {
            return false; // Already registered
        }
        let ibkr_symbol = Self::derive_ibkr_symbol(symbol, exchange_hint);

        let ibkr_exch = Self::ibkr_exchange(exchange_hint);
        eprintln!(
            "CONTRACT_REG: ticker={} sym={} ibkr_sym={} exch={}→{} ccy={}",
            ticker_id.0, symbol, ibkr_symbol,
            exchange_hint, ibkr_exch, currency_hint
        );
        let mapping = ContractMapping {
            ticker_id,
            symbol: symbol.to_string(),
            ibkr_symbol,
            exchange: exchange_hint.to_string(),
            currency: currency_hint.to_string(),
        };
        self.contract_map.insert(ticker_id, mapping);
        true
    }

    /// Derive IBKR-compatible symbol from our internal symbol + exchange.
    ///
    /// Exchange suffix rules (yfinance → IBKR):
    ///   .L  → strip (LSE/LSEETF)     .T  → strip (TSE/TSEJ)
    ///   .AX → strip (ASX)             .KS → strip (KRX/KSE)
    ///   .SI → strip (SGX)             .HK → strip (HKEX/SEHK)
    ///   .SS → strip (SSE)             .SZ → strip (SZSE)
    ///   .DE → strip (XETRA/IBIS)      .PA → strip (Euronext Paris)
    ///   .AS → strip (Euronext Amsterdam) .BR → strip (Euronext Brussels)
    ///   .LS → strip (Euronext Lisbon)  .MI → strip (Borsa Italiana)
    ///   .MC → strip (Bolsa Madrid)     .SW → strip (SIX Swiss)
    ///   .ST → strip (Nasdaq Stockholm) .OL → strip (Oslo Børs)
    ///   .CO → strip (Nasdaq Copenhagen) .HE → strip (Nasdaq Helsinki)
    ///
    /// Exchange-specific transforms:
    ///   HKEX: strip leading zeros (0001.HK → "1")
    ///   KRX:  keep leading zeros  (005930.KS → "005930")
    ///   TSE:  numeric codes as-is  (6758.T → "6758")
    fn derive_ibkr_symbol(symbol: &str, exchange: &str) -> String {
        // Strip all known exchange suffixes (yfinance convention)
        let suffixes = [
            ".L", ".T", ".AX", ".KS", ".SI", ".HK", ".SS", ".SZ",
            ".DE", ".PA", ".AS", ".BR", ".LS", ".MI", ".MC", ".SW",
            ".ST", ".OL", ".CO", ".HE",
        ];
        let base = suffixes.iter()
            .find_map(|suffix| symbol.strip_suffix(suffix))
            .unwrap_or(symbol);

        // Exchange-specific transformations
        let ibkr_sym = match exchange {
            // HKEX/SEHK: strip leading zeros from numeric codes (0001 → 1)
            "HKEX" | "SEHK" => base.trim_start_matches('0'),
            // KRX/KSE: IBKR uses the full numeric code WITH leading zeros
            // (005930 stays 005930). Do NOT strip.
            "KRX" | "KSE" => base,
            // All others: use base symbol as-is
            _ => base,
        };

        // Safety: if trimming emptied the string (e.g. symbol "0" on HKEX), use "0"
        let ibkr_sym = if ibkr_sym.is_empty() { "0" } else { ibkr_sym };
        ibkr_sym.to_string()
    }

    /// Check if a ticker_id has a registered contract.
    pub fn has_contract(&self, ticker_id: &TickerId) -> bool {
        self.contract_map.contains_key(ticker_id)
    }

    /// Map our internal/watchlist exchange names to IBKR API exchange codes.
    /// Covers all 6 market regions: Asia, Europe, US.
    fn ibkr_exchange(exchange: &str) -> &str {
        match exchange {
            // === Asian ===
            "TSE"          => "TSEJ",     // Tokyo Stock Exchange Japan
            "HKEX"         => "SEHK",     // Stock Exchange of Hong Kong
            "KRX"          => "KSE",      // Korea Exchange
            "SGX"          => "SGX",      // Singapore Exchange
            "ASX"          => "ASX",      // Australian Securities Exchange
            // === European ===
            "LSE"          => "LSE",      // London main market
            "LSEETF"       => "LSEETF",   // London ETF/ETP segment
            "XETRA"        => "IBIS",     // Frankfurt/XETRA
            "EURONEXT"     => "SBF",      // Euronext Paris (default)
            "EURONEXT_PA"  => "SBF",      // Euronext Paris
            "EURONEXT_AS"  => "AEB",      // Euronext Amsterdam
            "EURONEXT_BR"  => "BVME",     // Euronext Brussels
            "EURONEXT_LS"  => "BVL",      // Euronext Lisbon
            "SIX"          => "EBS",      // SIX Swiss Exchange (via IBKR code EBS)
            "XSTO"         => "SFB",      // Nasdaq Stockholm
            "XCSE"         => "XCSE",     // Nasdaq Copenhagen
            "XOSL"         => "OSE",      // Oslo Børs
            "XHEL"         => "HEX",      // Nasdaq Helsinki
            "XMIL"         => "BVME",     // Borsa Italiana (Milan)
            "XMAD"         => "BM",       // Bolsa de Madrid
            "AEB"          => "AEB",      // Euronext Amsterdam (legacy)
            "HEX"          => "HEX",      // Helsinki (legacy)
            "SFB"          => "SFB",      // Nasdaq Nordic (legacy)
            // === US ===
            "SMART"        => "SMART",    // IBKR Smart routing (US default)
            "NYSE"         => "SMART",    // Routed via SMART
            "NASDAQ"       => "SMART",    // Routed via SMART
            "AMEX"         => "SMART",    // Routed via SMART
            // Pass through anything else
            other          => other,
        }
    }

    /// Build an ibapi Contract from our config.
    /// Maps internal exchange names to IBKR API codes.
    ///
    /// US equities use SMART routing (best execution across venues).
    /// International equities use DIRECT exchange routing — SMART doesn't resolve
    /// KRX, TSEJ, SEHK, etc. properly (returns code=200 "No security definition").
    fn build_contract(mapping: &ContractMapping) -> Contract {
        let ibkr_exchange = Self::ibkr_exchange(&mapping.exchange);

        // US equities: SMART routing with no primary hint (IBKR resolves automatically)
        if ibkr_exchange == "SMART" {
            return Contract::stock(&mapping.ibkr_symbol)
                .on_exchange("SMART")
                .in_currency(&mapping.currency)
                .build();
        }

        // International equities: direct exchange routing
        // IBKR requires the actual exchange code (TSEJ, SEHK, KSE, IBIS, etc.)
        Contract::stock(&mapping.ibkr_symbol)
            .on_exchange(ibkr_exchange)
            .in_currency(&mapping.currency)
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

        let ibkr_exchange = Self::ibkr_exchange(&mapping.exchange);
        let contract = Self::build_contract(&mapping);
        eprintln!(
            "IBKR: Subscribing bars for {} → ibkr_sym={} exchange={} currency={}",
            mapping.symbol, mapping.ibkr_symbol, ibkr_exchange, mapping.currency
        );
        match client.realtime_bars(
            &contract,
            BarSize::Sec5,
            WhatToShow::Trades,
            TradingHours::Extended,
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
    /// Dual-track: reqRealTimeBars (primary, needs paid subs) + reqMktData (fallback, delayed OK).
    /// reqMktData emission is throttled to 5-second cadence in poll_ticks() to prevent
    /// backpressure thrashing that occurred when mktdata emitted on every tick.
    pub fn subscribe_all(&mut self) -> u32 {
        // IBKR limits ~100 concurrent market data subscriptions.
        // Dynamic Universe produces a live_100 via active_watchlist.json.
        // Subscribe those FIRST (they're the highest-conviction names),
        // then fill remaining slots round-robin across exchanges.
        const MAX_SUBSCRIPTIONS: usize = 100;

        // Phase 0: Load active_watchlist.json for live_100 priority list
        let watchlist_symbols: std::collections::HashSet<String> = {
            let watchlist_path = std::path::Path::new("config/active_watchlist.json");
            if watchlist_path.exists() {
                if let Ok(content) = std::fs::read_to_string(watchlist_path) {
                    if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&content) {
                        parsed.get("tickers")
                            .and_then(|v| v.as_array())
                            .map(|arr| arr.iter()
                                .filter_map(|v| v.as_str().map(String::from))
                                .collect())
                            .unwrap_or_default()
                    } else { std::collections::HashSet::new() }
                } else { std::collections::HashSet::new() }
            } else { std::collections::HashSet::new() }
        };
        eprintln!("SUBSCRIBE: active_watchlist has {} symbols", watchlist_symbols.len());

        // Phase 1: Subscribe watchlist tickers first (highest priority)
        let mut ticker_ids: Vec<TickerId> = Vec::with_capacity(MAX_SUBSCRIPTIONS);
        let mut subscribed_syms: std::collections::HashSet<String> = std::collections::HashSet::new();

        if !watchlist_symbols.is_empty() {
            // Find TickerIds matching watchlist symbols
            let mut watchlist_tids: Vec<TickerId> = self.contract_map.iter()
                .filter(|(_, m)| watchlist_symbols.contains(&m.symbol))
                .map(|(&tid, _)| tid)
                .collect();
            watchlist_tids.sort_by_key(|t| t.0);
            for &tid in &watchlist_tids {
                if ticker_ids.len() >= MAX_SUBSCRIPTIONS { break; }
                if let Some(m) = self.contract_map.get(&tid) {
                    if !subscribed_syms.contains(&m.symbol) {
                        ticker_ids.push(tid);
                        subscribed_syms.insert(m.symbol.clone());
                    }
                }
            }
            eprintln!("SUBSCRIBE: {} watchlist tickers queued", ticker_ids.len());
        }

        // Phase 2: Fill remaining slots — LSEETF leveraged ETPs (core edge)
        if ticker_ids.len() < MAX_SUBSCRIPTIONS {
            let mut lseetf: Vec<TickerId> = self.contract_map.iter()
                .filter(|(_, m)| m.exchange == "LSEETF" && !subscribed_syms.contains(&m.symbol))
                .map(|(&tid, _)| tid)
                .collect();
            lseetf.sort_by_key(|t| t.0);
            for &tid in &lseetf {
                if ticker_ids.len() >= MAX_SUBSCRIPTIONS { break; }
                if let Some(m) = self.contract_map.get(&tid) {
                    if !subscribed_syms.contains(&m.symbol) {
                        ticker_ids.push(tid);
                        subscribed_syms.insert(m.symbol.clone());
                    }
                }
            }
        }

        // Phase 3: Fill remaining round-robin across other exchanges
        let remaining = MAX_SUBSCRIPTIONS.saturating_sub(ticker_ids.len());
        if remaining > 0 {
            let mut by_exchange: std::collections::HashMap<String, Vec<TickerId>> = std::collections::HashMap::new();
            for (&tid, mapping) in &self.contract_map {
                if !subscribed_syms.contains(&mapping.symbol) {
                    by_exchange.entry(mapping.exchange.clone()).or_default().push(tid);
                }
            }
            for tids in by_exchange.values_mut() {
                tids.sort_by_key(|t| t.0);
            }
            let exchanges = ["LSE", "SMART", "TSE", "HKEX", "XETRA", "EURONEXT", "SGX"];
            let per_exch = remaining / exchanges.len().max(1);
            let mut extra_slots = remaining - per_exch * exchanges.len();
            for exch in &exchanges {
                if let Some(tids) = by_exchange.get(*exch) {
                    let slots = per_exch + if extra_slots > 0 { extra_slots -= 1; 1 } else { 0 };
                    for &tid in tids.iter().take(slots) {
                        if ticker_ids.len() >= MAX_SUBSCRIPTIONS { break; }
                        ticker_ids.push(tid);
                    }
                }
            }
        }

        // Execute subscriptions
        let mut mkt_count = 0u32;
        for &tid in &ticker_ids {
            if mkt_count as usize >= MAX_SUBSCRIPTIONS { break; }
            if self.subscribe_mktdata(tid).is_ok() {
                mkt_count += 1;
            }
        }

        eprintln!(
            "SUBSCRIBE: {} reqMktData (cap={}, watchlist={}, total_contracts={})",
            mkt_count, MAX_SUBSCRIPTIONS, watchlist_symbols.len(), self.contract_map.len()
        );
        mkt_count
    }

    /// Subscribe to reqMktData streaming for a contract (delayed-data compatible).
    /// This is the fallback when reqRealTimeBars fails (e.g. paper account without shared subs).
    pub fn subscribe_mktdata(&mut self, ticker_id: TickerId) -> Result<(), BrokerError> {
        let client = self.client.as_ref().ok_or(BrokerError::NotConnected)?;
        let mapping = self
            .contract_map
            .get(&ticker_id)
            .ok_or_else(|| {
                BrokerError::InvalidOrder(format!("No contract for ticker {}", ticker_id.0))
            })?
            .clone();

        let contract = Self::build_contract(&mapping);
        match client.market_data(&contract).subscribe() {
            Ok(sub) => {
                eprintln!(
                    "IBKR: MktData subscribed for {} (ticker_id={}, exchange={})",
                    mapping.symbol, ticker_id.0, mapping.exchange
                );
                self.mktdata_subs.push(MktDataSubscription {
                    ticker_id,
                    symbol: mapping.symbol,
                    sub,
                });
                self.tick_accum.insert(ticker_id, TickAccumulator::default());
                Ok(())
            }
            Err(e) => {
                eprintln!("IBKR: MktData subscription failed for {}: {e}", mapping.symbol);
                Err(BrokerError::InvalidOrder(format!("mktdata subscribe: {e}")))
            }
        }
    }

    /// Subscribe to reqMktData for all registered contracts (no bars, pure tick stream).
    pub fn subscribe_all_mktdata(&mut self) -> u32 {
        let ticker_ids: Vec<TickerId> = self.contract_map.keys().copied().collect();
        let mut count = 0u32;
        for tid in ticker_ids {
            if self.subscribe_mktdata(tid).is_ok() {
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
                self.l1_subscribed_set.insert(ticker_id);
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
            // Also remove from cache and L1 tracking set
            self.l1_cache.remove(&ticker_id);
            self.l1_subscribed_set.remove(&ticker_id);
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

    /// Return the current data drought severity level.
    /// 0 = no drought, 1 = severe (recommend reconnect), 2 = critical (recommend HALT).
    /// Caller (main loop) should check this and escalate risk regime accordingly.
    pub fn data_drought_level(&self) -> u8 {
        if self.zero_data_polls >= 3500 {
            2 // Critical — halt trading
        } else if self.zero_data_polls >= 1500 {
            1 // Severe — recommend reconnect
        } else {
            0 // Normal
        }
    }

    /// Return all registered TickerIds from the contract map.
    pub fn contract_map_keys(&self) -> Vec<TickerId> {
        self.contract_map.keys().copied().collect()
    }

    /// Return TickerIds that have active reqMktData subscriptions.
    /// Use this to limit L1 subscriptions to only tickers with data flowing.
    pub fn mktdata_subscribed_tids(&self) -> Vec<TickerId> {
        self.mktdata_subs.iter().map(|s| s.ticker_id).collect()
    }

    /// Return the symbol for a given TickerId, if registered.
    pub fn symbol_for(&self, tid: TickerId) -> Option<&str> {
        self.contract_map.get(&tid).map(|m| m.symbol.as_str())
    }

    /// Poll all bar subscriptions and L1 tick-by-tick subscriptions. Non-blocking.
    /// Includes ibapi bug #434 workaround: check subscription.error() for silent failures.
    pub fn poll_ticks(&mut self) {
        // 1. Drain L1 bid/ask ticks into cache (P0-01).
        let mut l1_count = 0u32;
        for l1_sub in &self.l1_subs {
            while let Some(ba) = l1_sub.sub.try_next() {
                l1_count += 1;
                if ba.bid_price > 0.0 && ba.ask_price > 0.0 {
                    self.l1_cache.insert(l1_sub.ticker_id, (ba.bid_price, ba.ask_price));
                }
            }
            // ibapi bug #434: Check for errors that got silently converted to ParseInt.
            // IBKR error 354 ("Not subscribed") arrives as "ParseInt: invalid digit" in ibapi.
            if let Some(err) = l1_sub.sub.error() {
                let err_str = format!("{err}");
                // Remove from L1 gate — this ticker doesn't actually have L1 data.
                self.l1_subscribed_set.remove(&l1_sub.ticker_id);
                if self.sub_errors_detected < 20 {
                    eprintln!(
                        "IBKR SUB ERROR [L1 {}]: {} (likely error 354 — no market data subscription shared to paper account)",
                        l1_sub.symbol, err_str
                    );
                }
                self.sub_errors_detected += 1;
            }
        }

        // 2. Drain bar subscriptions, using L1 cache for bid/ask when available.
        // Track which tickers got bar data this cycle to avoid duplicate ticks from mktdata.
        let mut bar_tickers_this_cycle: HashSet<TickerId> = HashSet::new();
        let mut bar_count = 0u32;
        for bar_sub in &self.bar_subs {
            while let Some(bar) = bar_sub.sub.try_next() {
                bar_count += 1;
                bar_tickers_this_cycle.insert(bar_sub.ticker_id);
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
                    bid_size: 0,
                    ask_size: 0,
                };
                self.pending_ticks.push_back(tick);
                // Store bar high/low for ATR calculation
                self.bar_high_low
                    .entry(bar_sub.ticker_id)
                    .or_default()
                    .push((bar.high, bar.low));
            }
            // ibapi bug #434: Check for errors on bar subscriptions too.
            if let Some(err) = bar_sub.sub.error() {
                let err_str = format!("{err}");
                if self.sub_errors_detected < 20 {
                    eprintln!(
                        "IBKR SUB ERROR [BAR {}]: {} (likely error 354/10168 — no market data subscription shared to paper account)",
                        bar_sub.symbol, err_str
                    );
                }
                self.sub_errors_detected += 1;
            }
        }

        // 3. Drain reqMktData streaming subscriptions (fallback path).
        // Accumulate bid/ask/last into TickAccumulator, but ONLY EMIT a MarketTick
        // every 5 seconds per ticker. This prevents the backpressure thrashing (31K+
        // halt/recover cycles) that occurred when emitting on every individual tick.
        let five_sec_ns: u64 = 5_000_000_000;
        let mut mkt_count = 0u32;
        for mkt_sub in &self.mktdata_subs {
            let has_bar_data = bar_tickers_this_cycle.contains(&mkt_sub.ticker_id);
            // Drain ALL pending ticks into accumulator (non-blocking, no emission here)
            while let Some(tick_type) = mkt_sub.sub.try_next() {
                match &tick_type {
                    TickTypes::Price(tp) => {
                        if let Some(acc) = self.tick_accum.get_mut(&mkt_sub.ticker_id) {
                            match tp.tick_type {
                                TickType::Bid | TickType::DelayedBid => {
                                    if tp.price > 0.0 { acc.bid = tp.price; acc.has_data = true; }
                                }
                                TickType::Ask | TickType::DelayedAsk => {
                                    if tp.price > 0.0 { acc.ask = tp.price; acc.has_data = true; }
                                }
                                TickType::Last | TickType::DelayedLast => {
                                    if tp.price > 0.0 {
                                        acc.last = tp.price;
                                        if acc.high == 0.0 || tp.price > acc.high { acc.high = tp.price; }
                                        if acc.low == 0.0 || tp.price < acc.low { acc.low = tp.price; }
                                        acc.has_data = true;
                                    }
                                }
                                TickType::High | TickType::DelayedHigh => {
                                    if tp.price > 0.0 { acc.high = tp.price; }
                                }
                                TickType::Low | TickType::DelayedLow => {
                                    if tp.price > 0.0 { acc.low = tp.price; }
                                }
                                _ => {}
                            }
                        }
                    }
                    TickTypes::Size(ts) => {
                        if let Some(acc) = self.tick_accum.get_mut(&mkt_sub.ticker_id) {
                            match ts.tick_type {
                                TickType::Volume | TickType::DelayedVolume => {
                                    acc.volume = ts.size as u64;
                                }
                                TickType::BidSize | TickType::DelayedBidSize => {
                                    acc.bid_size = ts.size as i32;
                                }
                                TickType::AskSize | TickType::DelayedAskSize => {
                                    acc.ask_size = ts.size as i32;
                                }
                                _ => {}
                            }
                        }
                    }
                    TickTypes::PriceSize(ps) => {
                        if let Some(acc) = self.tick_accum.get_mut(&mkt_sub.ticker_id) {
                            match ps.price_tick_type {
                                TickType::Bid | TickType::DelayedBid => {
                                    if ps.price > 0.0 {
                                        acc.bid = ps.price;
                                        acc.bid_size = ps.size as i32;
                                        acc.has_data = true;
                                    }
                                }
                                TickType::Ask | TickType::DelayedAsk => {
                                    if ps.price > 0.0 {
                                        acc.ask = ps.price;
                                        acc.ask_size = ps.size as i32;
                                        acc.has_data = true;
                                    }
                                }
                                TickType::Last | TickType::DelayedLast => {
                                    if ps.price > 0.0 {
                                        acc.last = ps.price;
                                        if acc.high == 0.0 || ps.price > acc.high { acc.high = ps.price; }
                                        if acc.low == 0.0 || ps.price < acc.low { acc.low = ps.price; }
                                        acc.has_data = true;
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                    TickTypes::Notice(notice) => {
                        if self.sub_errors_detected < 50 {
                            eprintln!(
                                "IBKR NOTICE [MKT {}]: {:?}",
                                mkt_sub.symbol, notice
                            );
                        }
                        self.sub_errors_detected += 1;
                    }
                    _ => {}
                }
            }
            // THROTTLED EMISSION: Emit synthetic bar every 5 seconds (matches reqRealTimeBars cadence).
            // Only emit if this ticker did NOT get bar data (dedup with reqRealTimeBars).
            if !has_bar_data {
                if let Some(acc) = self.tick_accum.get_mut(&mkt_sub.ticker_id) {
                    if acc.has_data && acc.last > 0.0
                        && self.now_ns >= acc.last_emit_ns + five_sec_ns
                    {
                        mkt_count += 1;
                        let tick = MarketTick {
                            ticker_id: mkt_sub.ticker_id,
                            bid: if acc.bid > 0.0 { acc.bid } else { acc.last * 0.999 },
                            ask: if acc.ask > 0.0 { acc.ask } else { acc.last * 1.001 },
                            last: acc.last,
                            volume: acc.volume,
                            timestamp_ns: self.now_ns,
                            recv_timestamp_ns: self.now_ns,
                            bid_size: acc.bid_size,
                            ask_size: acc.ask_size,
                        };
                        self.pending_ticks.push_back(tick);
                        // Store high/low for ATR calculation (synthetic bar)
                        if acc.high > 0.0 && acc.low > 0.0 {
                            self.bar_high_low
                                .entry(mkt_sub.ticker_id)
                                .or_default()
                                .push((acc.high, acc.low));
                        }
                        // Reset accumulator for next 5-second window
                        acc.high = acc.last;
                        acc.low = acc.last;
                        acc.last_emit_ns = self.now_ns;
                    }
                }
            }
            // Check for errors on mktdata subscriptions
            if let Some(err) = mkt_sub.sub.error() {
                if self.sub_errors_detected < 50 {
                    eprintln!(
                        "IBKR SUB ERROR [MKT {}]: {}",
                        mkt_sub.symbol, err
                    );
                }
                self.sub_errors_detected += 1;
            }
        }

        // Update lifetime counters
        self.total_bars_received += (bar_count + mkt_count) as u64;
        self.total_l1_received += l1_count as u64;

        // Diagnostic: log data arrival
        let any_data = bar_count > 0 || l1_count > 0 || mkt_count > 0;
        if any_data {
            let total = self.pending_ticks.len();
            let poll_num = self.total_bars_received;
            if poll_num <= 40 || poll_num % 720 == 0 {
                eprintln!(
                    "POLL: bars={} mkt={} l1={} pending={} bar_subs={} mkt_subs={} l1_subs={} l1_eligible={} lifetime={}",
                    bar_count, mkt_count, l1_count, total,
                    self.bar_subs.len(), self.mktdata_subs.len(), self.l1_subs.len(),
                    self.l1_subscribed_set.len(), self.total_bars_received
                );
            }
        }

        // Diagnostic: periodic warning if we have subscriptions but no data.
        let total_subs = self.bar_subs.len() + self.mktdata_subs.len() + self.l1_subs.len();
        if !any_data && total_subs > 0 {
            self.zero_data_polls += 1;
            // First warning at 500 polls (~60-120s), then every 3000 polls (~5 min)
            if self.zero_data_polls == 500 || (self.zero_data_polls > 500 && self.zero_data_polls % 3000 == 0) {
                eprintln!(
                    "DATA_DROUGHT: {} consecutive polls with ZERO data (bar_subs={} mkt_subs={} l1_subs={} sub_errors={} lifetime={})",
                    self.zero_data_polls, self.bar_subs.len(), self.mktdata_subs.len(),
                    self.l1_subs.len(), self.sub_errors_detected, self.total_bars_received
                );
                if self.sub_errors_detected > 0 && self.total_bars_received == 0 {
                    eprintln!(
                        "DATA_DROUGHT: DIAGNOSIS — {} sub errors, ZERO data. \
                         Likely causes: (1) LIVE TWS session blocking paper data (error 10197/420), \
                         (2) reqRealTimeBars needs paid exchange subs, (3) contract resolution failures. \
                         bar_subs={} mkt_subs={} l1_subs={}",
                        self.sub_errors_detected,
                        self.bar_subs.len(), self.mktdata_subs.len(), self.l1_subs.len()
                    );
                }
            }

            // Escalation thresholds: increasingly severe responses to prolonged data drought.
            // ~3500 polls ≈ 6-7 minutes of zero data at 100ms loop cadence.
            // ~1500 polls ≈ 2.5-3 minutes of zero data.
            if self.zero_data_polls >= 3500 {
                // Only log once at threshold crossing (not every poll)
                if self.zero_data_polls == 3500 || self.zero_data_polls % 3500 == 0 {
                    eprintln!(
                        "DATA DROUGHT CRITICAL: {} empty polls — escalating to HALT. \
                         No market data for ~{:.0} minutes. Engine should halt trading.",
                        self.zero_data_polls,
                        self.zero_data_polls as f64 * 0.1 / 60.0
                    );
                }
            } else if self.zero_data_polls >= 1500 {
                if self.zero_data_polls == 1500 || self.zero_data_polls % 1500 == 0 {
                    eprintln!(
                        "DATA DROUGHT SEVERE: {} empty polls — recommending reconnect. \
                         No market data for ~{:.0} minutes.",
                        self.zero_data_polls,
                        self.zero_data_polls as f64 * 0.1 / 60.0
                    );
                }
            }
        } else if any_data {
            // Reset drought counter on any data
            self.zero_data_polls = 0;
        }
    }

    /// Drain pending market ticks.
    pub fn drain_ticks(&mut self) -> Vec<MarketTick> {
        self.pending_ticks.drain(..).collect()
    }

    /// Poll broker events (order fills, status updates, errors). Non-blocking.
    /// Drains the ibapi order_update_stream subscription for fill detection.
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
            return;
        }

        // Drain order update subscription for fills and status updates.
        // Reverse-lookup IBKR order_id → our order_id via order_id_map.
        if let Some(ref sub) = self.order_update_sub {
            while let Some(update) = sub.try_next() {
                match update {
                    OrderUpdate::ExecutionData(exec) => {
                        let ibkr_id = exec.execution.order_id;
                        let our_id = self
                            .order_id_map
                            .iter()
                            .find(|(_, v)| **v == ibkr_id)
                            .map(|(k, _)| k.clone());

                        if let Some(our_order_id) = our_id {
                            // Resolve ticker_id from contract symbol
                            let exec_sym = exec.contract.symbol.as_str();
                            let ticker_id = self
                                .contract_map
                                .iter()
                                .find(|(_, m)| m.ibkr_symbol == exec_sym)
                                .map(|(&tid, _)| tid)
                                .unwrap_or(TickerId(0));

                            eprintln!(
                                "IBKR FILL: order={} ibkr_id={} ticker={} exec_id={} qty={} price={:.4} side={}",
                                our_order_id, ibkr_id, ticker_id.0,
                                exec.execution.execution_id, exec.execution.shares,
                                exec.execution.price, exec.execution.side
                            );
                            self.events.push_back(BrokerEvent::Fill {
                                order_id: our_order_id,
                                ticker_id,
                                filled_qty: exec.execution.shares as u32,
                                remaining_qty: 0, // Updated by OrderStatus
                                price: exec.execution.price,
                                exec_id: exec.execution.execution_id.clone(),
                                commission: 0.0, // Updated by CommissionReport
                            });
                        } else {
                            eprintln!(
                                "IBKR FILL (untracked): ibkr_id={} sym={} qty={} price={:.4}",
                                ibkr_id, exec.contract.symbol,
                                exec.execution.shares, exec.execution.price
                            );
                        }
                    }
                    OrderUpdate::CommissionReport(report) => {
                        eprintln!(
                            "IBKR COMMISSION: exec_id={} commission={:.2} currency={} pnl={:?}",
                            report.execution_id, report.commission,
                            report.currency, report.realized_pnl
                        );
                    }
                    OrderUpdate::OrderStatus(status) => {
                        let ibkr_id = status.order_id;
                        let our_id = self
                            .order_id_map
                            .iter()
                            .find(|(_, v)| **v == ibkr_id)
                            .map(|(k, _)| k.clone());

                        if let Some(our_order_id) = our_id {
                            eprintln!(
                                "IBKR STATUS: order={} ibkr_id={} status={} filled={} remaining={}",
                                our_order_id, ibkr_id, status.status,
                                status.filled, status.remaining
                            );
                        }
                    }
                    OrderUpdate::OpenOrder(_) => {
                        // Order acknowledged by exchange — already handled via Ack event.
                    }
                    OrderUpdate::Message(notice) => {
                        // Suppress noisy errors that flood logs:
                        // 10190: tick-by-tick subscription limit (harmless, bars are primary)
                        // 10197: competing live session (log once, then suppress)
                        // 420: competing session from different IP (log once, then suppress)
                        match notice.code {
                            10190 => {} // Suppress entirely — tick-by-tick limit is expected
                            10197 => {
                                // "No market data during competing live session"
                                // This means user's LIVE account is logged in elsewhere (TWS, mobile, other API)
                                // IBKR blocks paper account market data when live session is active
                                if self.sub_errors_detected < 5 {
                                    eprintln!(
                                        "IBKR BLOCKED: code=10197 — LIVE account session is active elsewhere. \
                                         Paper account market data is BLOCKED until live TWS/Gateway is closed. \
                                         Close TWS on your desktop/phone to unblock paper data."
                                    );
                                }
                                self.sub_errors_detected += 1;
                            }
                            420 => {
                                // "Trading TWS session is connected from a different IP address"
                                if self.sub_errors_detected < 5 {
                                    eprintln!(
                                        "IBKR BLOCKED: code=420 — TWS session connected from different IP. \
                                         Close competing TWS/Gateway sessions to unblock."
                                    );
                                }
                                self.sub_errors_detected += 1;
                            }
                            _ => {
                                eprintln!("IBKR ORDER NOTICE: code={} msg={}", notice.code, notice.message);
                            }
                        }
                    }
                }
            }
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

        // P1-2.11: Round up to exchange minimum lot size.
        let exchange_name = mapping.exchange.as_str();
        let min_lot = min_lot_for_exchange(exchange_name);
        let qty = if min_lot > 1 {
            ((qty as f64 / min_lot as f64).ceil() as u32) * min_lot
        } else {
            qty
        };

        // P1-2.12: Quantize limit price to exchange-appropriate tick size.
        let limit_price = quantize_price(limit_price, exchange_name);

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

        // P1-2.18: Cancel/replace throttle — 3 seconds between modifications per order.
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0);
        if let Some(&last_ns) = self.last_modify_ns.get(order_id) {
            if now - last_ns < 3_000_000_000 {
                eprintln!("CANCEL_THROTTLE: Order {} modified <3s ago, deferring cancel", order_id);
                return Err(BrokerError::PacingViolation);
            }
        }
        self.last_modify_ns.insert(order_id.to_string(), now);

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

    /// Re-subscribe to all market data after broker reconnection (Error 1102).
    /// Delegates to the inherent subscribe_all() which handles LSEETF prioritization and caps.
    fn resubscribe_all(&mut self) -> u32 {
        self.subscribe_all()
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

    /// P21: Check if a ticker has a registered contract.
    fn has_contract(&self, ticker_id: &TickerId) -> bool {
        self.contract_map.contains_key(ticker_id)
    }

    /// P21: Dynamically register a contract from watchlist metadata.
    fn register_dynamic_contract(
        &mut self,
        ticker_id: TickerId,
        symbol: &str,
        exchange: &str,
        currency: &str,
    ) -> bool {
        if self.contract_map.contains_key(&ticker_id) {
            return false;
        }
        let ibkr_symbol = IbkrBroker::derive_ibkr_symbol(symbol, exchange);
        let mapping = ContractMapping {
            ticker_id,
            symbol: symbol.to_string(),
            ibkr_symbol,
            exchange: exchange.to_string(),
            currency: currency.to_string(),
        };
        self.contract_map.insert(ticker_id, mapping);
        true
    }

    /// P21-FX: Get the trading currency for a ticker from its contract mapping.
    fn currency_for_ticker(&self, ticker_id: &TickerId) -> &str {
        self.contract_map
            .get(ticker_id)
            .map(|m| m.currency.as_str())
            .unwrap_or("GBP")
    }

    /// P21-FX: Get the exchange for a ticker from its contract mapping.
    fn exchange_for_ticker(&self, ticker_id: &TickerId) -> &str {
        self.contract_map
            .get(ticker_id)
            .map(|m| m.exchange.as_str())
            .unwrap_or("XLON")
    }

    fn is_l1_subscribed(&self, ticker_id: &TickerId) -> bool {
        self.l1_subscribed_set.contains(ticker_id)
    }

    fn symbol_for(&self, ticker_id: TickerId) -> Option<String> {
        self.contract_map.get(&ticker_id).map(|m| m.symbol.clone())
    }
}

/// P2-3.9: Ensure IBKR connection is cleanly closed when broker is dropped.
impl Drop for IbkrBroker {
    fn drop(&mut self) {
        eprintln!("IbkrBroker: Drop called — disconnecting from IBKR");
        self.disconnect();
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
        assert_eq!(config.port, 4003);
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

    // ── P1-2.11: Min lot size per exchange ──
    #[test]
    fn test_min_lot_western_exchanges() {
        assert_eq!(min_lot_for_exchange("LSEETF"), 1);
        assert_eq!(min_lot_for_exchange("LSE"), 1);
        assert_eq!(min_lot_for_exchange("SMART"), 1);
        assert_eq!(min_lot_for_exchange("IBIS"), 1);
        assert_eq!(min_lot_for_exchange("EURONEXT"), 1);
        assert_eq!(min_lot_for_exchange("ASX"), 1);
        assert_eq!(min_lot_for_exchange("KRX"), 1);
    }

    #[test]
    fn test_min_lot_asian_exchanges() {
        assert_eq!(min_lot_for_exchange("TSEJ"), 100);
        assert_eq!(min_lot_for_exchange("TSE"), 100);
        assert_eq!(min_lot_for_exchange("SEHK"), 100);
        assert_eq!(min_lot_for_exchange("HKEX"), 100);
        assert_eq!(min_lot_for_exchange("SGX"), 100);
    }

    #[test]
    fn test_min_lot_unknown_defaults_to_one() {
        assert_eq!(min_lot_for_exchange("UNKNOWN"), 1);
        assert_eq!(min_lot_for_exchange(""), 1);
    }

    #[test]
    fn test_lot_rounding() {
        // Simulate lot rounding for TSE (100-share lots)
        let min_lot: u32 = 100;
        let qty: u32 = 50;
        let rounded = ((qty as f64 / min_lot as f64).ceil() as u32) * min_lot;
        assert_eq!(rounded, 100); // 50 → rounds up to 100

        let qty: u32 = 150;
        let rounded = ((qty as f64 / min_lot as f64).ceil() as u32) * min_lot;
        assert_eq!(rounded, 200); // 150 → rounds up to 200

        let qty: u32 = 200;
        let rounded = ((qty as f64 / min_lot as f64).ceil() as u32) * min_lot;
        assert_eq!(rounded, 200); // 200 → stays 200 (exact multiple)

        let qty: u32 = 1;
        let rounded = ((qty as f64 / min_lot as f64).ceil() as u32) * min_lot;
        assert_eq!(rounded, 100); // 1 → rounds up to 100
    }

    // ── P1-2.12: Tick-size quantization ──
    #[test]
    fn test_quantize_price_lse() {
        // LSE: price < 0.5 → tick 0.0001
        let q = quantize_price(0.1234, "LSE");
        assert!((q - 0.1234).abs() < 1e-6);

        // LSE: price >= 10.0 → tick 0.01
        let q = quantize_price(15.123, "LSE");
        assert!((q - 15.12).abs() < 1e-6);

        // LSE: price 1.0..5.0 → tick 0.001
        let q = quantize_price(2.5678, "LSE");
        assert!((q - 2.568).abs() < 1e-6);
    }

    #[test]
    fn test_quantize_price_tse() {
        // TSE: price < 3000 → tick 1.0
        let q = quantize_price(2500.7, "TSE");
        assert!((q - 2501.0).abs() < 1e-6);

        // TSE: price 5000..30000 → tick 10.0
        let q = quantize_price(15003.0, "TSE");
        assert!((q - 15000.0).abs() < 1e-6);

        // TSE: price >= 30000 → tick 50.0
        let q = quantize_price(35025.0, "TSEJ");
        assert!((q - 35050.0).abs() < 1e-6);
    }

    #[test]
    fn test_quantize_price_default() {
        // Default: tick 0.01
        let q = quantize_price(123.456, "SMART");
        assert!((q - 123.46).abs() < 1e-6);

        let q = quantize_price(10.005, "NYSE");
        assert!((q - 10.01).abs() < 1e-6);
    }

    // ── P1-2.18: Cancel throttle ──
    #[test]
    fn test_cancel_throttle_map_initialized() {
        let broker = IbkrBroker::new(IbkrBrokerConfig::default());
        assert!(broker.last_modify_ns.is_empty());
    }
}
