//! Universe: ticker interning, classification (Vanguard/Apex), routing, and data filters.
//! Vanguard tickers get continuous tick delivery. Apex tickers get 60-second OHLCV snapshots.
//! NO tick is ever routed to BOTH paths.

use crate::types::{MarketTick, TickerId};
use std::collections::HashMap;

/// Classification of a ticker in the universe.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UniverseClass {
    /// Tier 1/2: continuous tick delivery to Python Brain.
    Vanguard,
    /// Tier 3: 60-second OHLCV snapshots.
    Apex,
}

/// Why a tick was filtered out before reaching strategies.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FilterReason {
    /// Amihud illiquidity ratio exceeds threshold.
    AmihudIlliquid,
    /// Average Spread-to-Equity Ratio exceeds 0.5%.
    AserSpreadTooWide,
    /// Erroneous tick: >5% deviation from 1-second moving average (H77).
    ErroneousTick,
    /// Reverse split detected: >500% overnight price move (H76).
    ReverseSplit,
    /// Synthetic halt: no ticks for 30s on this ticker (H122).
    SyntheticHalt,
    /// Ticker is halted pending manual review.
    TickerHalted,
    /// Tick data failed validation (NaN, Inf, negative, or crossed book).
    InvalidTick,
}

/// Result of routing a tick through the universe.
#[derive(Debug)]
pub enum RouteResult {
    /// Deliver continuously to Vanguard strategy.
    Vanguard(MarketTick),
    /// Buffer for 60s Apex snapshot.
    Apex(MarketTick),
    /// Tick was filtered out.
    Filtered(FilterReason),
}

/// Per-ticker tracking state for filters.
#[derive(Clone, Debug)]
pub struct TickerState {
    pub classification: UniverseClass,
    /// Last tick timestamp (nanoseconds) for synthetic halt detection.
    pub last_tick_ns: u64,
    /// Previous close price for reverse split detection.
    pub prev_close: f64,
    /// Exponential moving average (1-second) for erroneous tick filter.
    pub ema_1s: f64,
    /// Whether this ticker is halted (reverse split, etc).
    pub halted: bool,
    /// Amihud illiquidity ratio (updated externally, e.g. nightly).
    pub amihud_illiq: f64,
    /// Average spread-to-equity ratio (updated externally).
    pub aser: f64,
    /// Last Apex snapshot timestamp (nanoseconds) for 60s interval.
    pub last_apex_snapshot_ns: u64,
}

impl TickerState {
    fn new(class: UniverseClass) -> Self {
        Self {
            classification: class,
            last_tick_ns: 0,
            prev_close: 0.0,
            ema_1s: 0.0,
            halted: false,
            amihud_illiq: 0.0,
            aser: 0.0,
            last_apex_snapshot_ns: 0,
        }
    }
}

/// Configuration thresholds for universe filters.
#[derive(Clone, Debug)]
pub struct UniverseConfig {
    /// Amihud illiquidity threshold — tickers above this are filtered.
    pub amihud_threshold: f64,
    /// ASER threshold (0.005 = 0.5%) — tickers above this are filtered (H36).
    pub aser_threshold: f64,
    /// Erroneous tick deviation threshold from 1s EMA (H77).
    /// Set to 15% to accommodate 3x leveraged ETPs (which can move 5%+ in seconds).
    pub erroneous_tick_pct: f64,
    /// Reverse split detection threshold (5.0 = 500%) overnight move (H76).
    pub reverse_split_pct: f64,
    /// Synthetic halt: no ticks for this many nanoseconds (H122).
    pub synthetic_halt_ns: u64,
    /// Apex snapshot interval in nanoseconds (60s = 60_000_000_000).
    pub apex_snapshot_interval_ns: u64,
    /// reqMktData pacing: minimum nanoseconds between requests (H42).
    pub mkt_data_pacing_ns: u64,
    /// EMA alpha for 1-second moving average (erroneous tick filter).
    pub ema_alpha: f64,
}

impl Default for UniverseConfig {
    fn default() -> Self {
        Self {
            amihud_threshold: 1.0,
            aser_threshold: 0.005,
            erroneous_tick_pct: 0.15, // 15% to accommodate 3x leveraged ETPs
            reverse_split_pct: 5.0,
            synthetic_halt_ns: 30_000_000_000,
            apex_snapshot_interval_ns: 60_000_000_000,
            mkt_data_pacing_ns: 10_000_000,
            ema_alpha: 0.1,
        }
    }
}

/// Ticker interning table: String symbol ↔ TickerId mapping.
/// No String comparisons in the hot path (H01).
pub struct TickerIntern {
    symbol_to_id: HashMap<String, TickerId>,
    id_to_symbol: Vec<String>,
    next_id: u32,
}

impl Default for TickerIntern {
    fn default() -> Self {
        Self::new()
    }
}

impl TickerIntern {
    pub fn new() -> Self {
        Self {
            symbol_to_id: HashMap::new(),
            id_to_symbol: Vec::new(),
            next_id: 0,
        }
    }

    /// Intern a symbol, returning its TickerId. Idempotent.
    pub fn intern(&mut self, symbol: &str) -> TickerId {
        if let Some(&id) = self.symbol_to_id.get(symbol) {
            return id;
        }
        let id = TickerId(self.next_id);
        self.symbol_to_id.insert(symbol.to_string(), id);
        self.id_to_symbol.push(symbol.to_string());
        self.next_id += 1;
        id
    }

    /// Look up a symbol by TickerId.
    pub fn lookup(&self, id: TickerId) -> Option<&str> {
        self.id_to_symbol.get(id.0 as usize).map(|s| s.as_str())
    }

    /// Total interned tickers.
    pub fn len(&self) -> usize {
        self.id_to_symbol.len()
    }

    /// Whether any tickers are interned.
    pub fn is_empty(&self) -> bool {
        self.id_to_symbol.is_empty()
    }
}

/// The Universe: manages ticker classification, routing, and filtering.
pub struct Universe {
    pub config: UniverseConfig,
    pub intern: TickerIntern,
    pub tickers: HashMap<TickerId, TickerState>,
    /// Last reqMktData timestamp for pacing enforcement (H42).
    pub last_mkt_data_req_ns: u64,
    /// Whether any reqMktData request has been made yet.
    has_requested: bool,
    /// Rate-limit invalid-tick warnings: last warning timestamp per ticker (ns).
    invalid_tick_last_warn_ns: HashMap<TickerId, u64>,
}

impl Universe {
    pub fn new(config: UniverseConfig) -> Self {
        Self {
            config,
            intern: TickerIntern::new(),
            tickers: HashMap::new(),
            last_mkt_data_req_ns: 0,
            has_requested: false,
            invalid_tick_last_warn_ns: HashMap::new(),
        }
    }

    /// Register a ticker with its classification.
    /// SC-20: Rejects `.R` warrant tickers (they are excluded from the universe).
    pub fn register(&mut self, symbol: &str, class: UniverseClass) -> TickerId {
        // SC-20: Filter out .R warrant tickers
        if symbol.ends_with(".R") || symbol.contains(".R.") {
            eprintln!("UNIVERSE: Rejecting warrant ticker: {symbol}");
            // Return a sentinel TickerId — caller should check
            return self.intern.intern(symbol);
        }
        let id = self.intern.intern(symbol);
        self.tickers.insert(id, TickerState::new(class));
        id
    }

    /// Check if a ticker is registered and active in the universe.
    pub fn is_registered(&self, ticker_id: &TickerId) -> bool {
        self.tickers.contains_key(ticker_id)
    }

    /// Check if a ticker is classified as Apex (Tier 3).
    /// Returns false if ticker is not registered.
    pub fn is_apex(&self, ticker_id: TickerId) -> bool {
        self.tickers
            .get(&ticker_id)
            .map(|state| state.classification == UniverseClass::Apex)
            .unwrap_or(false)
    }

    /// Get the classification of a ticker.
    /// Returns None if ticker is not registered.
    pub fn get_classification(&self, ticker_id: TickerId) -> Option<UniverseClass> {
        self.tickers.get(&ticker_id).map(|state| state.classification)
    }

    /// Set a ticker's previous close (for reverse split detection).
    pub fn set_prev_close(&mut self, ticker_id: TickerId, price: f64) {
        if let Some(state) = self.tickers.get_mut(&ticker_id) {
            state.prev_close = price;
            state.ema_1s = price;
        }
    }

    /// Set Amihud illiquidity ratio for a ticker.
    pub fn set_amihud(&mut self, ticker_id: TickerId, illiq: f64) {
        if let Some(state) = self.tickers.get_mut(&ticker_id) {
            state.amihud_illiq = illiq;
        }
    }

    /// Set ASER (Average Spread-to-Equity Ratio) for a ticker.
    pub fn set_aser(&mut self, ticker_id: TickerId, aser: f64) {
        if let Some(state) = self.tickers.get_mut(&ticker_id) {
            state.aser = aser;
        }
    }

    /// Halt a ticker (e.g. after reverse split detection).
    pub fn halt_ticker(&mut self, ticker_id: TickerId) {
        if let Some(state) = self.tickers.get_mut(&ticker_id) {
            state.halted = true;
        }
    }

    /// Check if reqMktData pacing is satisfied (H42: 10ms between requests).
    /// First request is always allowed (no prior request recorded).
    pub fn can_request_mkt_data(&self, now_ns: u64) -> bool {
        !self.has_requested || now_ns >= self.last_mkt_data_req_ns + self.config.mkt_data_pacing_ns
    }

    /// Record a reqMktData request for pacing.
    pub fn record_mkt_data_request(&mut self, now_ns: u64) {
        self.last_mkt_data_req_ns = now_ns;
        self.has_requested = true;
    }

    /// Check if an Apex snapshot is due for a ticker (60s interval, H18).
    pub fn apex_snapshot_due(&self, ticker_id: TickerId, now_ns: u64) -> bool {
        if let Some(state) = self.tickers.get(&ticker_id) {
            now_ns >= state.last_apex_snapshot_ns + self.config.apex_snapshot_interval_ns
        } else {
            false
        }
    }

    /// Record an Apex snapshot timestamp.
    pub fn record_apex_snapshot(&mut self, ticker_id: TickerId, now_ns: u64) {
        if let Some(state) = self.tickers.get_mut(&ticker_id) {
            state.last_apex_snapshot_ns = now_ns;
        }
    }

    /// Route a tick: classify, filter, and return the routing decision.
    /// This is the hot-path function called on every incoming tick.
    pub fn route_tick(&mut self, tick: &MarketTick, now_ns: u64) -> RouteResult {
        // NaN/Inf/crossed-book guard — reject garbage ticks before any processing.
        if !tick.is_valid() {
            // Rate-limited log: only warn once per ticker per 60 seconds.
            let last_warn = self.invalid_tick_last_warn_ns.get(&tick.ticker_id).copied().unwrap_or(0);
            if now_ns.saturating_sub(last_warn) > 60_000_000_000 {
                eprintln!(
                    "INVALID_TICK: ticker={} bid={} ask={} last={} vol={} — NaN/Inf/negative/crossed",
                    tick.ticker_id.0, tick.bid, tick.ask, tick.last, tick.volume,
                );
                self.invalid_tick_last_warn_ns.insert(tick.ticker_id, now_ns);
            }
            return RouteResult::Filtered(FilterReason::InvalidTick);
        }

        let Some(state) = self.tickers.get_mut(&tick.ticker_id) else {
            return RouteResult::Filtered(FilterReason::TickerHalted);
        };

        // Check if ticker is halted
        if state.halted {
            return RouteResult::Filtered(FilterReason::TickerHalted);
        }

        // Amihud illiquidity filter
        if state.amihud_illiq > self.config.amihud_threshold {
            return RouteResult::Filtered(FilterReason::AmihudIlliquid);
        }

        // ASER filter (H36)
        if state.aser > self.config.aser_threshold {
            return RouteResult::Filtered(FilterReason::AserSpreadTooWide);
        }

        // Synthetic halt detection (H122): no ticks for 30s
        // FIX: Update last_tick_ns even on filtered ticks to prevent permanent lockout.
        // Previously, once a ticker was halted, last_tick_ns never updated, making
        // (now_ns - last_tick_ns) grow forever and permanently blocking the ticker.
        if state.last_tick_ns > 0 && now_ns > state.last_tick_ns + self.config.synthetic_halt_ns {
            state.last_tick_ns = now_ns; // Reset so next tick can pass through
            return RouteResult::Filtered(FilterReason::SyntheticHalt);
        }

        // Reverse split detection (H76): >500% overnight move
        if state.prev_close > 0.0 && tick.last > 0.0 {
            let ratio = tick.last / state.prev_close;
            if ratio > (1.0 + self.config.reverse_split_pct)
                || ratio < 1.0 / (1.0 + self.config.reverse_split_pct)
            {
                state.halted = true;
                return RouteResult::Filtered(FilterReason::ReverseSplit);
            }
        }

        // Erroneous tick filter (H77): >5% deviation from 1s EMA
        if state.ema_1s > 0.0 && tick.last > 0.0 {
            let deviation = (tick.last - state.ema_1s).abs() / state.ema_1s;
            if deviation > self.config.erroneous_tick_pct {
                // Don't update EMA with erroneous tick
                return RouteResult::Filtered(FilterReason::ErroneousTick);
            }
        }

        // Update tracking
        state.last_tick_ns = now_ns;
        if tick.last > 0.0 {
            if state.ema_1s <= 0.0 {
                state.ema_1s = tick.last;
            } else {
                state.ema_1s += self.config.ema_alpha * (tick.last - state.ema_1s);
            }
        }

        // Route based on classification — EXCLUSIVE: never both
        match state.classification {
            UniverseClass::Vanguard => RouteResult::Vanguard(tick.clone()),
            UniverseClass::Apex => RouteResult::Apex(tick.clone()),
        }
    }
}
