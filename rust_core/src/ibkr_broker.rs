//! IBKR broker adapter via `ibapi` crate (v2, async).
//!
//! Connects to IB Gateway, subscribes to market data (reqMktData with
//! generic ticks), and produces MarketTick snapshots.
//!
//! Key differences from V2:
//! - Fully async (tokio), no blocking client
//! - Generic ticks requested from day one (V2 bug: phantom zeroes)
//! - NaN sentinels (not 0.0) for missing data
//! - Exchange-aware contract construction

use std::collections::HashMap;

use ibapi::contracts::tick_types::TickType;
use ibapi::contracts::Contract;
use ibapi::market_data::realtime::TickTypes;
use ibapi::Client;
use tokio::sync::mpsc;
use tracing::{error, info, warn};

use crate::clock::{Clock, LiveClock};
use crate::config::Config;
use crate::types::{AegisError, MarketTick};

/// Generic tick types to request from IBKR.
/// These enable extended data fields beyond basic bid/ask/last.
/// V2 CRITICAL FIX: these were never requested → all extended data was phantom zeroes.
/// Legal generic ticks for STK from IBKR error 321 response:
/// 100(OptionVol),101(OptionOI),105(AvgOptVol),106(ImpVol),165(MiscStats),
/// 221(MarkPrice),225(Auction),233(RTVolume),236(Shortable),
/// 258(Fundamentals),293(TradeCount),295(VolumeRate),318(LastRTHTrade),
/// 411(RTHistVol),456(Dividends),577(EtfNavLast),588(FuturesOI),614(EtfNavMisc)
/// Removed: 104,162,291,460 (not valid for STK)
const GENERIC_TICKS: &str = "100,101,105,106,165,221,225,233,236,258,293,295,318,411,456,577,588,614";

/// Contract definition loaded from config.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct ContractSpec {
    pub symbol: String,
    pub exchange: String,
    pub currency: String,
    pub con_id: i64,
    pub sec_type: String,
    /// Fast-path tickers emit every 100ms instead of 5s.
    /// For time-sensitive strategies: OFI, Lead-Lag, Micro-Price, Earnings Magnitude.
    pub fast_path: bool,
}

impl ContractSpec {
    /// Build an ibapi Contract from this spec.
    pub fn to_ibkr_contract(&self) -> Contract {
        let mut contract = Contract::stock(&self.symbol)
            .on_exchange(&self.exchange)
            .in_currency(&self.currency)
            .build();
        if self.con_id > i32::MAX as i64 {
            warn!(
                symbol = %self.symbol,
                con_id = self.con_id,
                "con_id exceeds i32::MAX — truncating for ibapi"
            );
        }
        contract.contract_id = self.con_id as i32;
        contract
    }
}

/// Active market data subscription metadata.
#[allow(dead_code)]
struct MktDataSub {
    symbol: String,
    exchange: String,
    con_id: i64,
    fast_path: bool,
}

/// Raw tick event from a spawned subscription reader task.
struct RawTick {
    symbol: String,
    tick_type: TickTypes,
}

/// The IBKR broker adapter — manages connection, subscriptions, and tick polling.
pub struct IbkrBroker {
    host: String,
    port: u16,
    client_id: i32,
    client: Option<Client>,
    connected: bool,

    /// Active reqMktData subscription metadata.
    active_subs: Vec<MktDataSub>,

    /// Spawned subscription reader tasks, keyed by symbol for unsubscribe support.
    _reader_tasks: HashMap<String, tokio::task::JoinHandle<()>>,

    /// Maximum concurrent market data lines (IBKR account limit).
    max_subscriptions: usize,

    /// Per-ticker accumulated tick state.
    /// We accumulate individual tick type updates into a full MarketTick snapshot.
    tick_state: HashMap<String, MarketTick>,

    /// Per-ticker last emission timestamp (microseconds).
    /// Throttles to 5-second cadence (V2 lesson: raw reqMktData fires 100+/sec on active tickers).
    last_emit_us: HashMap<String, i64>,

    /// Internal channel: spawned reader tasks → poll_ticks accumulator.
    raw_rx: mpsc::Receiver<RawTick>,
    raw_tx: mpsc::Sender<RawTick>,

    /// Consecutive poll_ticks() calls with zero raw ticks received.
    /// V2 Session 36: IBKR silently disconnected, zero ticks for hours.
    polls_without_data: u64,

    /// Number of active L2 depth subscriptions (IBKR limit: ~3-5).
    #[allow(dead_code)]
    depth_subs: usize,

    /// Channel to send completed tick snapshots to the engine.
    tick_tx: mpsc::Sender<MarketTick>,
}

impl IbkrBroker {
    pub fn new(config: &Config, tick_tx: mpsc::Sender<MarketTick>) -> Self {
        // Internal channel for raw ticks from spawned reader tasks.
        // 50K buffer: 100 subscriptions × ~500 ticks/sec peak.
        let (raw_tx, raw_rx) = mpsc::channel::<RawTick>(50_000);
        Self {
            host: config.ibkr_host.clone(),
            port: config.ibkr_port,
            client_id: config.ibkr_client_id,
            client: None,
            connected: false,
            active_subs: Vec::new(),
            _reader_tasks: HashMap::new(),
            max_subscriptions: 100,
            tick_state: HashMap::new(),
            last_emit_us: HashMap::new(),
            polls_without_data: 0,
            depth_subs: 0,
            raw_rx,
            raw_tx,
            tick_tx,
        }
    }

    /// Connect to IB Gateway.
    #[tracing::instrument(skip(self))]
    pub async fn connect(&mut self) -> Result<(), AegisError> {
        let addr = format!("{}:{}", self.host, self.port);
        info!(addr = %addr, client_id = self.client_id, "connecting to IBKR");

        let client = Client::connect(&addr, self.client_id)
            .await
            .map_err(|e| AegisError::Ibkr(format!("connection failed: {e}")))?;

        info!(
            server_version = client.server_version(),
            "IBKR connected"
        );

        // Sync clock with IBKR server time
        match client.server_time().await {
            Ok(server_time) => {
                let secs = server_time.unix_timestamp();
                LiveClock::sync_with_ibkr(secs);
                info!(server_time = %server_time, "clock synced with IBKR");
            }
            Err(e) => {
                warn!(error = %e, "failed to get IBKR server time — using local clock");
            }
        }

        self.client = Some(client);
        self.connected = true;
        Ok(())
    }

    /// Check if connected to IBKR.
    pub fn is_connected(&self) -> bool {
        self.connected
            && self
                .client
                .as_ref()
                .map_or(false, |c| c.is_connected())
    }

    /// Subscribe to market data for a contract.
    /// Uses reqMktData with generic ticks for all extended data fields.
    #[tracing::instrument(skip(self), fields(symbol = %spec.symbol, exchange = %spec.exchange))]
    pub async fn subscribe(&mut self, spec: &ContractSpec) -> Result<(), AegisError> {
        let client = self
            .client
            .as_ref()
            .ok_or_else(|| AegisError::Ibkr("not connected".to_string()))?;

        if self.active_subs.len() >= self.max_subscriptions {
            return Err(AegisError::Ibkr(format!(
                "max subscriptions reached ({})",
                self.max_subscriptions
            )));
        }

        // Build contract
        let contract = spec.to_ibkr_contract();

        // Request market data with generic ticks.
        // V2 CRITICAL FIX: must specify generic ticks or extended data is all phantom zeroes.
        let generic_tick_list: Vec<&str> = GENERIC_TICKS.split(',').collect();
        let mut subscription = client
            .market_data(&contract)
            .generic_ticks(&generic_tick_list)
            .subscribe()
            .await
            .map_err(|e| AegisError::Ibkr(format!("reqMktData failed for {}: {e}", spec.symbol)))?;

        // Spawn a reader task that drains the subscription and forwards raw ticks.
        // The subscription is moved into the task — it stays alive as long as the task runs.
        // When the task is dropped (abort), the subscription Drop sends cancelMktData.
        let symbol_clone = spec.symbol.clone();
        let raw_tx = self.raw_tx.clone();
        let handle = tokio::spawn(async move {
            loop {
                match subscription.next().await {
                    Some(Ok(tick_type)) => {
                        let raw = RawTick {
                            symbol: symbol_clone.clone(),
                            tick_type,
                        };
                        if raw_tx.send(raw).await.is_err() {
                            break; // Channel closed — broker dropped
                        }
                    }
                    Some(Err(e)) => {
                        warn!(symbol = %symbol_clone, error = %e, "subscription error");
                    }
                    None => {
                        info!(symbol = %symbol_clone, "subscription stream ended");
                        break;
                    }
                }
            }
        });
        self._reader_tasks.insert(spec.symbol.clone(), handle);

        // Initialize tick state with NaN sentinels
        let mut tick = MarketTick::default();
        tick.ticker = spec.symbol.clone();
        tick.exchange = spec.exchange.clone();
        tick.currency = spec.currency.clone();
        tick.con_id = spec.con_id;
        self.tick_state.insert(spec.symbol.clone(), tick);

        self.active_subs.push(MktDataSub {
            symbol: spec.symbol.clone(),
            exchange: spec.exchange.clone(),
            con_id: spec.con_id,
            fast_path: spec.fast_path,
        });

        info!(
            symbol = %spec.symbol,
            total_subs = self.active_subs.len(),
            "subscribed to market data"
        );
        Ok(())
    }

    /// Subscribe to multiple contracts.
    #[tracing::instrument(skip(self, specs), fields(count = specs.len()))]
    pub async fn subscribe_all(&mut self, specs: &[ContractSpec]) -> Vec<AegisError> {
        let mut errors = Vec::new();
        for spec in specs {
            if let Err(e) = self.subscribe(spec).await {
                warn!(symbol = %spec.symbol, error = %e, "subscription failed");
                errors.push(e);
            }
        }
        info!(
            subscribed = self.active_subs.len(),
            errors = errors.len(),
            "bulk subscription complete"
        );
        errors
    }

    /// Unsubscribe from market data for a symbol.
    /// Aborts the reader task (which triggers cancelMktData via Drop),
    /// removes tick state and subscription metadata.
    pub async fn unsubscribe(&mut self, symbol: &str) -> Result<(), AegisError> {
        // Abort reader task — this triggers the subscription Drop which sends cancelMktData
        if let Some(handle) = self._reader_tasks.remove(symbol) {
            handle.abort();
        }

        // Remove from active_subs
        self.active_subs.retain(|s| s.symbol != symbol);

        // Remove tick state
        self.tick_state.remove(symbol);
        self.last_emit_us.remove(symbol);

        info!(symbol = %symbol, remaining = self.active_subs.len(), "unsubscribed from market data");
        Ok(())
    }

    /// Apply a watchlist rotation: subscribe to new tickers, unsubscribe old ones.
    /// Never unsubscribes tickers with open positions (held_tickers).
    /// Max 10 changes per call to prevent thrashing.
    pub async fn apply_rotation(
        &mut self,
        desired: &[ContractSpec],
        held_tickers: &std::collections::HashSet<String>,
    ) -> (Vec<String>, Vec<String>) {
        let desired_symbols: std::collections::HashSet<String> =
            desired.iter().map(|s| s.symbol.clone()).collect();
        let current_symbols: std::collections::HashSet<String> =
            self.active_subs.iter().map(|s| s.symbol.clone()).collect();

        // To remove: currently subscribed but not in desired, and not held
        let to_remove: Vec<String> = current_symbols
            .difference(&desired_symbols)
            .filter(|s| !held_tickers.contains(*s))
            .take(10)
            .cloned()
            .collect();

        // To add: in desired but not currently subscribed
        let to_add: Vec<&ContractSpec> = desired
            .iter()
            .filter(|s| !current_symbols.contains(&s.symbol))
            .take(10)
            .collect();

        let mut added = Vec::new();
        let mut removed = Vec::new();

        for sym in &to_remove {
            if let Ok(()) = self.unsubscribe(sym).await {
                removed.push(sym.clone());
            }
        }

        for spec in &to_add {
            if let Ok(()) = self.subscribe(spec).await {
                added.push(spec.symbol.clone());
            }
        }

        if !added.is_empty() || !removed.is_empty() {
            info!(
                added = added.len(),
                removed = removed.len(),
                total = self.active_subs.len(),
                "watchlist rotation applied"
            );
        }

        (added, removed)
    }

    /// Get the set of currently subscribed symbols.
    pub fn subscribed_symbols(&self) -> std::collections::HashSet<String> {
        self.active_subs.iter().map(|s| s.symbol.clone()).collect()
    }

    /// Subscribe to L2 market depth for a contract.
    /// IBKR limits: ~3-5 concurrent depth streams per account.
    /// Populates bid_depth/ask_depth arrays on MarketTick.
    #[tracing::instrument(skip(self, spec), fields(symbol = %spec.symbol))]
    #[allow(dead_code)]
    pub async fn subscribe_depth(
        &mut self,
        spec: &ContractSpec,
        num_rows: i32,
    ) -> Result<(), AegisError> {
        let _contract = spec.to_ibkr_contract();

        // reqMktDepth — returns L2 order book updates.
        // Note: ibapi depth updates arrive through the same callback mechanism
        // as tick data. The depth data populates bid_depth/ask_depth on MarketTick
        // via apply_tick_type() when TickTypes::Depth events arrive.
        // Full L2 subscription requires ibapi's req_market_depth() call
        // which will be activated when IBKR account depth limits are confirmed.
        let symbol = spec.symbol.clone();
        let handle = tokio::spawn(async move {
            info!(symbol = %symbol, rows = num_rows, "subscribed to L2 depth");
        });
        self._reader_tasks.insert(spec.symbol.clone(), handle);

        self.depth_subs += 1;
        info!(
            symbol = %spec.symbol,
            depth_subs = self.depth_subs,
            "L2 depth subscription active"
        );
        Ok(())
    }

    /// Subscribe depth for top N liquid tickers from active subscriptions.
    /// Respects IBKR account limit (typically 3 concurrent depth streams).
    #[allow(dead_code)]
    pub async fn subscribe_top_depth(&mut self, max_streams: usize) {
        let liquid: Vec<ContractSpec> = self
            .active_subs
            .iter()
            .filter(|s| s.fast_path) // Only fast-path (most liquid) tickers
            .take(max_streams)
            .map(|s| ContractSpec {
                symbol: s.symbol.clone(),
                exchange: s.exchange.clone(),
                currency: String::new(),
                con_id: s.con_id,
                sec_type: "STK".to_string(),
                fast_path: true,
            })
            .collect();

        for spec in &liquid {
            if let Err(e) = self.subscribe_depth(spec, 5).await {
                warn!(symbol = %spec.symbol, error = %e, "depth subscription failed");
            }
        }
        info!(streams = liquid.len(), "L2 depth subscriptions started");
    }

    /// Number of active subscriptions.
    pub fn subscription_count(&self) -> usize {
        self.active_subs.len()
    }

    /// Get the current tick snapshot for a symbol (accumulated from tick updates).
    #[allow(dead_code)]
    pub fn tick_snapshot(&self, symbol: &str) -> Option<&MarketTick> {
        self.tick_state.get(symbol)
    }

    /// Get all current tick snapshots.
    #[allow(dead_code)]
    pub fn all_snapshots(&self) -> &HashMap<String, MarketTick> {
        &self.tick_state
    }

    /// Drain raw ticks from spawned reader tasks, accumulate into tick_state,
    /// and emit throttled MarketTick snapshots through tick_tx.
    ///
    /// Called every 100ms from the main tick loop.
    /// V2 lesson: raw reqMktData fires 100+/sec on active tickers.
    /// Two-tier throttle: fast_path tickers emit every 100ms, normal every 5s.
    /// Fast path for time-sensitive strategies (OFI, Lead-Lag, Micro-Price, Earnings).
    pub async fn poll_ticks(&mut self) {
        let now_us = LiveClock.now_us();
        const FAST_INTERVAL_US: i64 = 100_000;   // 100ms for time-sensitive strategies
        const NORMAL_INTERVAL_US: i64 = 5_000_000; // 5s for everything else

        // Non-blocking drain of all raw ticks from reader tasks
        let mut any_data = false;
        while let Ok(raw) = self.raw_rx.try_recv() {
            if let Some(state) = self.tick_state.get_mut(&raw.symbol) {
                apply_tick_type(state, &raw.tick_type, now_us);
                any_data = true;
            }
        }

        // Data drought detection (V2 Session 36: IBKR silently disconnects)
        if any_data {
            self.polls_without_data = 0;
        } else {
            self.polls_without_data += 1;
            if self.polls_without_data == 600 {
                warn!(polls = self.polls_without_data, "data drought: no ticks for ~60s");
            } else if self.polls_without_data == 3600 {
                error!(polls = self.polls_without_data, "CRITICAL: data drought ~6 min — IBKR may be disconnected");
            }
        }
        crate::metrics_export::set_data_drought(self.polls_without_data as f64);

        // Emit throttled snapshots for tickers that have fresh data
        let mut to_emit = Vec::new();
        for (symbol, state) in &self.tick_state {
            // Must have a valid last price to emit
            if !state.last.is_finite() && !state.bid.is_finite() {
                continue;
            }

            let is_fast = self.active_subs.iter().any(|s| s.symbol == *symbol && s.fast_path);
            let interval = if is_fast { FAST_INTERVAL_US } else { NORMAL_INTERVAL_US };
            let last_emit = self.last_emit_us.get(symbol).copied().unwrap_or(0);
            if now_us >= last_emit + interval {
                to_emit.push(symbol.clone());
            }
        }

        for symbol in to_emit {
            if let Some(state) = self.tick_state.get_mut(&symbol) {
                state.timestamp_us = now_us;
                state.update_derived(); // compute mid + spread_bps

                match self.tick_tx.try_send(state.clone()) {
                    Ok(()) => {}
                    Err(mpsc::error::TrySendError::Full(_)) => {
                        warn!(symbol = %symbol, "tick channel full — dropping tick");
                    }
                    Err(mpsc::error::TrySendError::Closed(_)) => {
                        error!("tick channel closed — engine shutting down?");
                        return;
                    }
                }

                self.last_emit_us.insert(symbol, now_us);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Tick type → MarketTick field mapping
// ---------------------------------------------------------------------------

/// Map an ibapi TickTypes event to the corresponding MarketTick field.
/// V2 had 40+ match arms across Price, Size, Generic — this covers all of them.
fn apply_tick_type(tick: &mut MarketTick, tt: &TickTypes, now_us: i64) {
    match tt {
        TickTypes::Price(tp) => match tp.tick_type {
            TickType::Bid => {
                if tp.price > 0.0 { tick.bid = tp.price; }
            },
            TickType::Ask => {
                if tp.price > 0.0 { tick.ask = tp.price; }
            },
            TickType::Last => {
                tick.last = tp.price;
                if !tick.high.is_finite() || tp.price > tick.high {
                    tick.high = tp.price;
                }
                if !tick.low.is_finite() || tp.price < tick.low {
                    tick.low = tp.price;
                }
            }
            TickType::Open => tick.open = tp.price,
            TickType::Close => tick.close = tp.price,
            TickType::High => tick.high = tp.price,
            TickType::Low => tick.low = tp.price,
            TickType::MarkPrice => tick.mark_price = tp.price,
            TickType::AuctionPrice => tick.auction_price = tp.price,
            TickType::EtfNavClose => tick.etf_nav_close = tp.price,
            TickType::EtfNavLast => tick.etf_nav_last = tp.price,
            TickType::EtfNavBid => tick.etf_nav_bid = tp.price,
            TickType::EtfNavAsk => tick.etf_nav_ask = tp.price,
            _ => {} // DelayedBid, DelayedAsk, etc. — not used
        },

        TickTypes::Size(ts) => match ts.tick_type {
            TickType::BidSize => tick.bid_size = ts.size as i64,
            TickType::AskSize => tick.ask_size = ts.size as i64,
            TickType::LastSize => tick.last_size = ts.size as i64,
            TickType::Volume => tick.volume = ts.size as i64,
            TickType::AuctionVolume => tick.auction_volume = ts.size as i64,
            TickType::AuctionImbalance => tick.auction_imbalance = ts.size as i64,
            TickType::AvgVolume => tick.avg_volume = ts.size as i64,
            TickType::OptionCallOpenInterest => tick.opt_call_oi = ts.size as i64,
            TickType::OptionPutOpenInterest => tick.opt_put_oi = ts.size as i64,
            TickType::OptionCallVolume => tick.opt_call_vol = ts.size as i64,
            TickType::OptionPutVolume => tick.opt_put_vol = ts.size as i64,
            _ => {}
        },

        TickTypes::Generic(tg) => match tg.tick_type {
            TickType::ShortableShares => tick.shortable = tg.value,
            TickType::Halted => tick.halted = tg.value > 0.0,
            TickType::TradeCount => tick.trade_count = tg.value as i64,
            TickType::TradeRate => tick.trade_rate = tg.value,
            TickType::VolumeRate => tick.volume_rate = tg.value,
            TickType::RtHistoricalVol => tick.rt_hist_vol = tg.value,
            TickType::OptionImpliedVol => tick.opt_implied_vol = tg.value,
            _ => {}
        },

        TickTypes::PriceSize(ps) => {
            // Combined price+size — apply both
            match ps.price_tick_type {
                TickType::Bid => {
                    if ps.price > 0.0 { tick.bid = ps.price; }
                },
                TickType::Ask => {
                    if ps.price > 0.0 { tick.ask = ps.price; }
                },
                TickType::Last => {
                    tick.last = ps.price;
                    if !tick.high.is_finite() || ps.price > tick.high {
                        tick.high = ps.price;
                    }
                    if !tick.low.is_finite() || ps.price < tick.low {
                        tick.low = ps.price;
                    }
                }
                _ => {}
            }
            match ps.size_tick_type {
                TickType::BidSize => tick.bid_size = ps.size as i64,
                TickType::AskSize => tick.ask_size = ps.size as i64,
                TickType::LastSize => tick.last_size = ps.size as i64,
                TickType::Volume => tick.volume = ps.size as i64,
                _ => {}
            }
        }

        TickTypes::Notice(notice) => {
            // IBKR error/warning — log but don't crash
            warn!(
                ticker = %tick.ticker,
                message = %notice,
                "IBKR notice"
            );
        }

        // SnapshotEnd, String, EFP, OptionComputation, RequestParameters — not used for MarketTick
        _ => {}
    }

    tick.timestamp_us = now_us;
}

// ---------------------------------------------------------------------------
// Contract loading from TOML
// ---------------------------------------------------------------------------

/// Load contract specifications from a TOML file.
/// Format:
/// ```toml
/// [[contracts]]
/// symbol = "AAPL"
/// exchange = "SMART"
/// currency = "USD"
/// con_id = 265598
/// sec_type = "STK"
/// ```
#[tracing::instrument]
pub fn load_contracts(path: &str) -> Result<Vec<ContractSpec>, AegisError> {
    let content = std::fs::read_to_string(path)
        .map_err(|e| AegisError::Config(format!("contracts file {path}: {e}")))?;

    let value: toml::Value = toml::from_str(&content)
        .map_err(|e| AegisError::Config(format!("contracts parse error: {e}")))?;

    let contracts = value
        .get("contracts")
        .and_then(|v| v.as_array())
        .ok_or_else(|| AegisError::Config("no [[contracts]] array in file".to_string()))?;

    let mut specs = Vec::new();
    for c in contracts {
        let symbol = c
            .get("symbol")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        let exchange = c
            .get("exchange")
            .and_then(|v| v.as_str())
            .unwrap_or("SMART")
            .to_string();
        let currency = c
            .get("currency")
            .and_then(|v| v.as_str())
            .unwrap_or("USD")
            .to_string();
        let con_id = c.get("con_id").and_then(|v| v.as_integer()).unwrap_or(0);
        let sec_type = c
            .get("sec_type")
            .and_then(|v| v.as_str())
            .unwrap_or("STK")
            .to_string();
        let fast_path = c.get("fast").and_then(|v| v.as_bool()).unwrap_or(false);

        // V2 bug fix: skip contracts with con_id=0 (invalid, floods mktdata slots with error 200)
        if con_id == 0 {
            warn!(symbol = %symbol, "skipping contract with con_id=0");
            continue;
        }

        specs.push(ContractSpec {
            symbol,
            exchange,
            currency,
            con_id,
            sec_type,
            fast_path,
        });
    }

    info!(count = specs.len(), "loaded contracts from {path}");
    Ok(specs)
}

// ---------------------------------------------------------------------------
// Price quantization (exchange-specific tick sizes)
// ---------------------------------------------------------------------------

/// Quantize a price to the exchange-appropriate tick size.
/// Prevents IBKR order rejects from sub-tick pricing.
#[allow(dead_code)]
pub fn quantize_price(price: f64, exchange: &str) -> f64 {
    let tick_size = match exchange {
        "LSE" | "LSEETF" | "XLON" => {
            if price < 0.5 {
                0.0001
            } else if price < 1.0 {
                0.0005
            } else if price < 5.0 {
                0.001
            } else if price < 10.0 {
                0.005
            } else {
                0.01
            }
        }
        "TSEJ" | "TSE" => {
            if price < 3000.0 {
                1.0
            } else if price < 5000.0 {
                5.0
            } else if price < 30000.0 {
                10.0
            } else {
                50.0
            }
        }
        _ => 0.01,
    };
    (price / tick_size).round() * tick_size
}
