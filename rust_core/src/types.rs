//! Core types for AEGIS V4.
//!
//! MarketTick, Bar, Indicators, QuantState, BrainSignal, PositionState,
//! PortfolioState, and all supporting enums.
//!
//! Invariant #6: NaN = no data. -1.0 is never a sentinel.

use std::collections::HashMap;

use serde::{Deserialize, Serialize, Serializer};

/// Serialize f64 as JSON null when NaN/Inf (serde_json rejects NaN by default).
/// Applied via `#[serde(serialize_with = "serialize_f64_nan")]` on MarketTick fields.
fn serialize_f64_nan<S: Serializer>(v: &f64, s: S) -> Result<S::Ok, S::Error> {
    if v.is_finite() {
        s.serialize_f64(*v)
    } else {
        s.serialize_none()
    }
}

/// Serialize f64 array of tuples, converting NaN to null.
fn serialize_depth<S: Serializer>(v: &[(f64, i64); 5], s: S) -> Result<S::Ok, S::Error> {
    use serde::ser::SerializeSeq;
    let mut seq = s.serialize_seq(Some(5))?;
    for (price, size) in v {
        if price.is_finite() {
            seq.serialize_element(&(*price, *size))?;
        } else {
            seq.serialize_element(&(Option::<f64>::None, *size))?;
        }
    }
    seq.end()
}

// ---------------------------------------------------------------------------
// Identity / classification enums
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Direction {
    Long,
    Short,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Account {
    Isa,
    Ig,
    Gia,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Regime {
    Steady,
    Inflation,
    Woi,
    Crisis,
}

impl Regime {
    /// Scale factor from config regime_thresholds.regime_scale.
    /// Defaults: Steady=1.0, Inflation=0.80, Woi=0.53, Crisis=0.27.
    /// Ouroboros updates nightly based on per-regime P&L.
    pub fn scale(&self, regime_scale: &HashMap<String, f64>) -> f64 {
        let key = match self {
            Self::Steady => "steady",
            Self::Inflation => "inflation",
            Self::Woi => "woi",
            Self::Crisis => "crisis",
        };
        regime_scale.get(key).copied().unwrap_or(match self {
            Self::Steady => 1.0,
            Self::Inflation => 0.80,
            Self::Woi => 0.53,
            Self::Crisis => 0.27,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum DrawdownLevel {
    Normal,
    Yellow,
    Orange,
    Red,
    Black,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Timeframe {
    Min1,
    Min5,
    Min15,
    Hour1,
}

impl Timeframe {
    /// Duration of one bar in microseconds.
    pub fn duration_us(&self) -> i64 {
        match self {
            Self::Min1 => 60_000_000,
            Self::Min5 => 300_000_000,
            Self::Min15 => 900_000_000,
            Self::Hour1 => 3_600_000_000,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ExecutionTier {
    Market,
    Urgent,
    Patient,
    PegMid,
    ArrivalPrice,
    LimitOnly,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    Paper,
    Live,
}

// ---------------------------------------------------------------------------
// Exit methods — per-strategy, not one-size-fits-all
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ExitMethod {
    Chandelier {
        atr_mult: f64,
        rungs: Vec<(f64, f64)>,
    },
    FixedDay {
        hold_days: u16,
        profit_target_pct: Option<f64>,
        stop_atr: Option<f64>,
    },
    EventWindow {
        window_hours: f64,
        stop_atr: f64,
    },
    NextOpen,
    ProfitTarget {
        target_pct: f64,
        max_hold_hours: f64,
        stop_atr: f64,
    },
}

impl ExitMethod {
    /// Get the ATR multiplier used for stop distance (for risk calculation).
    pub fn atr_mult(&self) -> f64 {
        match self {
            Self::Chandelier { atr_mult, .. } => *atr_mult,
            Self::FixedDay { stop_atr, .. } => stop_atr.unwrap_or(3.0),
            Self::EventWindow { stop_atr, .. } => *stop_atr,
            Self::NextOpen => 3.0,
            Self::ProfitTarget { stop_atr, .. } => *stop_atr,
        }
    }
}

// ---------------------------------------------------------------------------
// MarketTick — every field from IBKR reqMktData + computed
// ---------------------------------------------------------------------------

/// 42-field market tick. All f64 default to NaN (invariant #6).
/// All i64 default to 0, booleans to false.
/// NaN f64 fields serialize as JSON null (serde_json rejects NaN).
#[derive(Debug, Clone, Serialize)]
pub struct MarketTick {
    // Identity
    pub con_id: i64,
    pub ticker: String,
    pub exchange: String,
    pub currency: String,
    pub timestamp_us: i64,

    // L1 core
    #[serde(serialize_with = "serialize_f64_nan")]
    pub bid: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub ask: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub last: f64,
    pub volume: i64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub open: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub high: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub low: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub close: f64,

    // L1 extended
    pub last_size: i64,
    pub bid_size: i64,
    pub ask_size: i64,
    pub trade_count: i64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub trade_rate: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub volume_rate: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub rt_hist_vol: f64,
    pub avg_volume: i64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub shortable: f64,
    pub halted: bool,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub mark_price: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub auction_price: f64,
    pub auction_volume: i64,
    pub auction_imbalance: i64,

    // ETF NAV
    #[serde(serialize_with = "serialize_f64_nan")]
    pub etf_nav_close: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub etf_nav_last: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub etf_nav_bid: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub etf_nav_ask: f64,

    // Options overlay
    pub opt_call_oi: i64,
    pub opt_put_oi: i64,
    pub opt_call_vol: i64,
    pub opt_put_vol: i64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub opt_implied_vol: f64,

    // Computed (by Rust, not IBKR)
    #[serde(serialize_with = "serialize_f64_nan")]
    pub spread_bps: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub mid: f64,

    // L2 depth (top 5 levels)
    #[serde(serialize_with = "serialize_depth")]
    pub bid_depth: [(f64, i64); 5],
    #[serde(serialize_with = "serialize_depth")]
    pub ask_depth: [(f64, i64); 5],
    #[serde(serialize_with = "serialize_f64_nan")]
    pub book_imbalance: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub book_pressure: f64,
}

impl Default for MarketTick {
    fn default() -> Self {
        Self {
            con_id: 0,
            ticker: String::new(),
            exchange: String::new(),
            currency: String::new(),
            timestamp_us: 0,

            bid: f64::NAN,
            ask: f64::NAN,
            last: f64::NAN,
            volume: 0,
            open: f64::NAN,
            high: f64::NAN,
            low: f64::NAN,
            close: f64::NAN,

            last_size: 0,
            bid_size: 0,
            ask_size: 0,
            trade_count: 0,
            trade_rate: f64::NAN,
            volume_rate: f64::NAN,
            rt_hist_vol: f64::NAN,
            avg_volume: 0,
            shortable: f64::NAN,
            halted: false,
            mark_price: f64::NAN,
            auction_price: f64::NAN,
            auction_volume: 0,
            auction_imbalance: 0,

            etf_nav_close: f64::NAN,
            etf_nav_last: f64::NAN,
            etf_nav_bid: f64::NAN,
            etf_nav_ask: f64::NAN,

            opt_call_oi: 0,
            opt_put_oi: 0,
            opt_call_vol: 0,
            opt_put_vol: 0,
            opt_implied_vol: f64::NAN,

            spread_bps: f64::NAN,
            mid: f64::NAN,

            bid_depth: [(f64::NAN, 0); 5],
            ask_depth: [(f64::NAN, 0); 5],
            book_imbalance: f64::NAN,
            book_pressure: f64::NAN,
        }
    }
}

impl MarketTick {
    /// Recompute spread_bps and mid from current bid/ask.
    pub fn update_derived(&mut self) {
        if self.bid.is_finite() && self.ask.is_finite() && self.ask > 0.0 {
            self.mid = (self.bid + self.ask) / 2.0;
            if self.mid > 0.0 {
                self.spread_bps = (self.ask - self.bid) / self.mid * 10_000.0;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Bar
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct Bar {
    pub ticker: String,
    pub timeframe: Timeframe,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
    pub vwap: f64,
    pub timestamp_us: i64,
    pub bar_count: u64,
    /// Cumulative price*volume for true VWAP calculation.
    #[serde(skip)]
    cum_pv: f64,
}

impl Bar {
    /// Create a new bar from the first tick in the period.
    pub fn from_tick(ticker: &str, timeframe: Timeframe, price: f64, volume: i64, ts: i64) -> Self {
        let cum_pv = price * volume as f64;
        Self {
            ticker: ticker.to_string(),
            timeframe,
            open: price,
            high: price,
            low: price,
            close: price,
            volume,
            vwap: price,
            timestamp_us: ts,
            bar_count: 1,
            cum_pv,
        }
    }

    /// Update this bar with a new tick.
    pub fn update(&mut self, price: f64, volume: i64) {
        if price > self.high {
            self.high = price;
        }
        if price < self.low {
            self.low = price;
        }
        self.close = price;
        self.volume += volume;
        self.bar_count += 1;

        // True VWAP: cumulative(price * volume) / cumulative(volume)
        self.cum_pv += price * volume as f64;
        if self.volume > 0 {
            self.vwap = self.cum_pv / self.volume as f64;
        }
    }
}

// ---------------------------------------------------------------------------
// Indicators — computed from bars
// ---------------------------------------------------------------------------

/// All indicator values for a single ticker at a point in time.
/// Invariant #6: NaN means not enough data yet.
#[derive(Debug, Clone, Serialize)]
pub struct Indicators {
    #[serde(serialize_with = "serialize_f64_nan")]
    pub rsi_14: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub atr_14: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub vwap: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub ibs: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub adx_10: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub hurst: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub ema_9: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub ema_21: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub rvol: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub bollinger_upper: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub bollinger_lower: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub keltner_upper: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub keltner_lower: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub macd: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub macd_signal: f64,
}

impl Default for Indicators {
    fn default() -> Self {
        Self {
            rsi_14: f64::NAN,
            atr_14: f64::NAN,
            vwap: f64::NAN,
            ibs: f64::NAN,
            adx_10: f64::NAN,
            hurst: f64::NAN,
            ema_9: f64::NAN,
            ema_21: f64::NAN,
            rvol: f64::NAN,
            bollinger_upper: f64::NAN,
            bollinger_lower: f64::NAN,
            keltner_upper: f64::NAN,
            keltner_lower: f64::NAN,
            macd: f64::NAN,
            macd_signal: f64::NAN,
        }
    }
}

// ---------------------------------------------------------------------------
// QuantState — GARCH, EVT, Kalman, H-Y
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize)]
pub struct QuantState {
    #[serde(serialize_with = "serialize_f64_nan")]
    pub garch_vol: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub garch_vol_annualized: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub evt_var_95: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub evt_cvar_95: f64,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub kalman_filtered: f64,
    pub kalman_is_spike: bool,
    #[serde(serialize_with = "serialize_f64_nan")]
    pub hy_correlation: f64,
}

impl Default for QuantState {
    fn default() -> Self {
        Self {
            garch_vol: f64::NAN,
            garch_vol_annualized: f64::NAN,
            evt_var_95: f64::NAN,
            evt_cvar_95: f64::NAN,
            kalman_filtered: f64::NAN,
            kalman_is_spike: false,
            hy_correlation: f64::NAN,
        }
    }
}

// ---------------------------------------------------------------------------
// BrainSignal — resolved trade from Python Instrument Selector → Rust
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrainSignal {
    pub signal_id: String,
    pub view_id: String,
    pub strategy: String,
    pub category: String,
    pub ticker: String,
    pub exchange: String,
    pub direction: Direction,
    pub conviction: u8,
    pub magnitude_pct: f64,
    pub thesis: String,
    pub risk_note: String,
    pub target_size_gbp: f64,
    pub stop_distance_atr: f64,
    /// Reference price for risk calculations (current price at signal time).
    /// Used by risk_arbiter to normalize stop_distance_atr to a fraction.
    /// Set by engine.rs from latest_ticks after deserialization.
    #[serde(default)]
    pub reference_price: f64,
    pub account: Account,
    pub leverage_embedded: f64,
    pub exit_method: ExitMethod,
    pub execution_tier: ExecutionTier,
    pub instrument_rationale: String,
    pub same_catalyst_group: Option<String>,
    pub timestamp_us: i64,
    /// Session 15: IG spread bet — guaranteed stop offset in price units.
    /// Only populated when account=Ig. Ignored for IBKR routes.
    #[serde(default)]
    pub guaranteed_stop: Option<f64>,
    /// Session 15: IG per-£-per-point sizing for spread bets.
    /// Required when account=Ig. Ignored for IBKR routes.
    #[serde(default)]
    pub per_point_gbp: Option<f64>,
}

// ---------------------------------------------------------------------------
// PositionState — open position tracked by Rust engine
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct PositionState {
    pub position_id: String,
    pub ticker: String,
    pub exchange: String,
    pub direction: Direction,
    pub qty: i64,
    pub entry_price: f64,
    pub entry_time_us: i64,
    pub current_price: f64,
    pub unrealised_pnl: f64,
    pub realised_pnl: f64,

    // Exit state
    pub exit_method: ExitMethod,
    pub highest_high: f64,
    pub lowest_low: f64,
    pub current_stop: f64,
    pub chandelier_rung: u8,
    pub exit_deadline_us: Option<i64>,

    // Risk
    pub notional: f64,
    pub risk_gbp: f64,
    pub account: Account,
    pub leverage_embedded: f64,
    pub same_catalyst_group: Option<String>,

    // Metadata
    pub strategy: String,
    pub category: String,
    pub signal_id: String,
    pub thesis: String,

    /// Session 18 P2: IG deal ID returned by `confirm()`. Only populated when
    /// the position was opened via `IgBroker`. Used by `check_exits` to route
    /// exits back to IG's `close()` endpoint. `None` for IBKR positions.
    pub ig_deal_id: Option<String>,
}

// ---------------------------------------------------------------------------
// PortfolioState — aggregate portfolio view
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct PortfolioState {
    pub equity_total: f64,
    pub equity_isa: f64,
    pub equity_ig: f64,
    pub equity_gia: f64,
    pub hwm: f64,
    pub drawdown_pct: f64,
    pub drawdown_level: DrawdownLevel,
    pub daily_pnl: f64,
    pub rolling_5d_pnl: f64,
    pub positions: HashMap<String, PositionState>,
    pub settled_cash: f64,
    pub unsettled_cash: f64,
    pub heat_pct: f64,
    pub active_pool: f64,
    pub barbell_allocation: f64,
    pub orders_this_minute: u32,
    pub regime: Regime,
    pub regime_probs: HashMap<String, f64>,
    pub recovery_day: u8,
}

impl Default for PortfolioState {
    fn default() -> Self {
        Self {
            equity_total: 0.0,
            equity_isa: 0.0,
            equity_ig: 0.0,
            equity_gia: 0.0,
            hwm: 0.0,
            drawdown_pct: 0.0,
            drawdown_level: DrawdownLevel::Normal,
            daily_pnl: 0.0,
            rolling_5d_pnl: 0.0,
            positions: HashMap::new(),
            settled_cash: 0.0,
            unsettled_cash: 0.0,
            heat_pct: 0.0,
            active_pool: 0.0,
            barbell_allocation: 0.0,
            orders_this_minute: 0,
            regime: Regime::Steady,
            regime_probs: HashMap::new(),
            recovery_day: 0,
        }
    }
}

// ---------------------------------------------------------------------------
// StrategyView — strategy output, instrument-agnostic
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
#[allow(dead_code)]
pub struct StrategyView {
    pub view_id: String,
    pub strategy: String,
    pub category: String,
    pub underlying: String,
    pub direction: Direction,
    pub conviction: u8,
    pub magnitude_pct: f64,
    pub expected_hold_hours: f64,
    pub urgency: u8,
    pub thesis: String,
    pub risk_note: String,
    pub catalyst_group: Option<String>,
    pub timestamp_us: i64,
}

// ---------------------------------------------------------------------------
// TickEnvelope — enriched payload sent to Python brain (Phase 3)
// ---------------------------------------------------------------------------

/// Wraps tick batch + indicators + quant + bars + regime + portfolio for Python strategies.
/// Sent as JSON over TCP (4-byte length prefix) on every bridge_cycle().
#[derive(Debug, Clone, Serialize)]
pub struct TickEnvelope {
    pub ticks: Vec<MarketTick>,
    pub indicators: HashMap<String, Indicators>,
    pub quant: HashMap<String, QuantState>,
    /// Per-ticker recent 1-min bars (last 20) for strategy pattern analysis.
    pub bars: HashMap<String, Vec<Bar>>,
    pub regime: String,
    pub regime_probs: HashMap<String, f64>,
    pub portfolio: PortfolioSummary,
}

/// Lightweight serializable subset of PortfolioState for Python brain.
#[derive(Debug, Clone, Serialize)]
pub struct PortfolioSummary {
    pub equity_total: f64,
    pub equity_isa: f64,
    pub equity_ig: f64,
    pub equity_gia: f64,
    pub drawdown_pct: f64,
    pub drawdown_level: String,
    pub daily_pnl: f64,
    pub heat_pct: f64,
    pub active_pool: f64,
    pub regime: String,
    pub open_positions: usize,
    pub open_tickers: Vec<String>,
}

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
#[allow(dead_code)]
pub enum AegisError {
    #[error("IBKR error: {0}")]
    Ibkr(String),

    #[error("Config error: {0}")]
    Config(String),

    #[error("Bridge error: {0}")]
    Bridge(String),

    #[error("Risk error: {0}")]
    Risk(String),

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Serialization error: {0}")]
    Serde(#[from] serde_json::Error),
}
