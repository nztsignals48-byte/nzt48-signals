//! Live/Paper Engine — 8-step startup, tick processing, reconciliation, shutdown.
//! Generic over BrokerAdapter: works with PaperBroker (testing) or IbkrBroker (live).

use chrono::Timelike;
use crate::asian_session::AsianSession;
use crate::broker::{BrokerAdapter, BrokerError, BrokerEvent, min_lot_for_exchange};
use crate::clock::{Clock, TradingMode};
use crate::config_loader::EngineConfig;
use serde_json;
use crate::cross_timezone::CrossTimezoneEngine;
use crate::currency::FxRateTable;
use crate::european_session::EuropeanSession;
use crate::exchange_profile::ExchangeRegistry;
use crate::exit_engine::{ChandelierStrategy, ExitConfig, ExitEngine, Executioner, OrderLifecycle, TrackedOrder, initial_stop_price};
use crate::garch_evt::EvtRegistry;
use crate::garch_inference::GarchRegistry;
use crate::hayashi_yoshida::HayashiYoshidaEngine;
use crate::portfolio::PortfolioState;
use crate::isa_gate::IsaGate;
use crate::overnight_carry::CarryManager;
use crate::scanner::{HotScanner, RotationScanner};
use crate::smart_router::SmartRouter;
use crate::subscription_manager::SubscriptionManager;
use crate::session_manager::SessionMode;
use crate::market_config::MarketConfig;
use crate::telemetry::Telemetry;
use crate::hardening::{CircuitBreaker, PanicGuard, Watchdog};
use crate::cross_asset_macro::CrossAssetMacro;
use crate::multiframe_vol::{MultiFrameVol, TimeFrame};
use crate::sector_rotation::{SectorHeatTracker, sector_for_ticker};
use crate::predictive_scoring::PredictiveScorer;
use crate::quote_imbalance::QuoteImbalanceDetector;
use crate::split_handler::SplitHandler;
use crate::liquidation_defense::LiquidationDefense;
use crate::broker_resilience::BrokerHealthMonitor;
use crate::wal_compressor::WalCompressor;
use crate::state_checkpoint::CheckpointManager;
use crate::session_manager::SessionManager;
use crate::latency_profiler::{LatencyProfiler, PipelineStage};
use crate::student_t_kalman::StudentTKalmanFilter;
use crate::log_thompson_sampler::LogThompsonSampler;
use crate::python_bridge::BrainSignal;
use crate::regime_detector::RegimeDetector;
// Dead code removed: EarlyRunnerDetector (never instantiated, VanguardSniper handles all entries)
use crate::position_sizer::KellyCalculator;
use crate::market_scheduler::{self, TradingSession};
use crate::reconciler;
use crate::risk_arbiter::{EvalContext, RiskArbiter};
use crate::types::{
    Direction, MarketTick, OrderSide, OrderState, PositionState, RiskRegime, TickerId, WalPayload,
};
use crate::universe::{RouteResult, Universe, UniverseClass, UniverseConfig};
use crate::wal_writer::{WalWriter, make_wal_event};
use std::collections::{HashMap, VecDeque};

/// P2-3.18: Promoted constants for compiler optimization.
const EXIT_COOLDOWN_NS: u64 = 5 * 60 * 1_000_000_000; // 5 minutes
const HALT_COOLDOWN_NS: u64 = 5 * 60 * 1_000_000_000; // 5 minutes
// P2-#22: STALE_TICK_MS removed — now read from config.hardening.stale_tick_ms

/// Errors from engine operations.
#[derive(Debug)]
pub enum EngineError {
    BrokerNotConnected,
    BrokerError(BrokerError),
    WalUnavailable,
    ReconciliationMismatch(usize),
    UnresolvedOrphans(Vec<String>),
    ConfigError(String),
}

impl From<BrokerError> for EngineError {
    fn from(e: BrokerError) -> Self {
        EngineError::BrokerError(e)
    }
}

impl std::fmt::Display for EngineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EngineError::BrokerNotConnected => write!(f, "Broker not connected"),
            EngineError::BrokerError(e) => write!(f, "Broker: {e}"),
            EngineError::WalUnavailable => write!(f, "WAL unavailable"),
            EngineError::ReconciliationMismatch(n) => write!(f, "{n} position mismatches"),
            EngineError::UnresolvedOrphans(ids) => write!(f, "Unresolved orphans: {ids:?}"),
            EngineError::ConfigError(msg) => write!(f, "Config: {msg}"),
        }
    }
}

/// Result of the 8-step startup sequence.
#[derive(Debug)]
pub struct StartupResult {
    pub wal_events_replayed: u64,
    pub positions_reconciled: u32,
    pub orphans_found: usize,
    pub clock_offset_secs: f64,
    pub tickers_registered: usize,
}

/// 60-second OHLCV candle aggregator for Apex tickers.
/// Tracks open, high, low, close, and volume over a 60-second window.
#[derive(Clone, Debug)]
pub struct ApexCandle {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: u64,
    pub start_ns: u64,
    pub end_ns: u64,
}

impl ApexCandle {
    pub fn new(price: f64, volume: u64, start_ns: u64) -> Self {
        Self {
            open: price,
            high: price,
            low: price,
            close: price,
            volume,
            start_ns,
            end_ns: start_ns + 60_000_000_000, // 60 seconds
        }
    }

    pub fn update(&mut self, price: f64, volume: u64) {
        self.high = self.high.max(price);
        self.low = self.low.min(price);
        self.close = price;
        self.volume = self.volume.saturating_add(volume);
    }

    pub fn is_complete(&self, now_ns: u64) -> bool {
        now_ns >= self.end_ns
    }
}

/// Rolling bar history per ticker for ATR calculation.
#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct BarHistory {
    pub highs: VecDeque<f64>,
    pub lows: VecDeque<f64>,
    pub closes: VecDeque<f64>,
    pub volumes: VecDeque<u64>,
    max_bars: usize,
}

impl BarHistory {
    pub fn new(max_bars: usize) -> Self {
        Self {
            highs: VecDeque::with_capacity(max_bars),
            lows: VecDeque::with_capacity(max_bars),
            closes: VecDeque::with_capacity(max_bars),
            volumes: VecDeque::with_capacity(max_bars),
            max_bars,
        }
    }

    fn push(&mut self, high: f64, low: f64, close: f64, volume: u64) {
        if self.closes.len() >= self.max_bars {
            self.highs.pop_front();
            self.lows.pop_front();
            self.closes.pop_front();
            self.volumes.pop_front();
        }
        self.highs.push_back(high);
        self.lows.push_back(low);
        self.closes.push_back(close);
        self.volumes.push_back(volume);
    }

    /// Wilder's ATR over the last `period` bars.
    pub fn atr(&self, period: usize) -> f64 {
        let n = self.closes.len();
        if n < 2 {
            // Fallback: 2% of last close
            return self.closes.back().copied().unwrap_or(1.0) * 0.02;
        }

        // True Range for each bar (starting from index 1)
        let start = if n > period + 1 { n - period - 1 } else { 0 };
        let mut atr_val = 0.0;
        for (count, i) in ((start + 1)..n).enumerate() {
            let high = self.highs[i];
            let low = self.lows[i];
            let prev_close = self.closes[i - 1];
            let tr = (high - low)
                .max((high - prev_close).abs())
                .max((low - prev_close).abs());
            if count == 0 {
                atr_val = tr;
            } else {
                // Wilder's smoothing: ATR = ATR_prev * (period-1)/period + TR/period
                let p = period.min(count + 1) as f64;
                atr_val = atr_val * (p - 1.0) / p + tr / p;
            }
        }

        if atr_val <= 0.0 {
            self.closes.back().copied().unwrap_or(1.0) * 0.02
        } else {
            atr_val
        }
    }

    /// Latest bar high (for Python bridge).
    pub fn last_high(&self) -> f64 {
        self.highs.back().copied().unwrap_or(0.0)
    }

    /// Latest bar low (for Python bridge).
    pub fn last_low(&self) -> f64 {
        self.lows.back().copied().unwrap_or(0.0)
    }

    pub fn len(&self) -> usize {
        self.closes.len()
    }

    pub fn is_empty(&self) -> bool {
        self.closes.is_empty()
    }

    /// Annualized realized volatility from log returns of close prices.
    /// Uses 5-second bars → ~6120 bars/day for LSE (8.5h × 720 bars/h).
    /// Returns 0.30 (default) if insufficient data (<10 bars).
    pub fn realized_vol(&self, bars_per_day: f64) -> f64 {
        let n = self.closes.len();
        if n < 10 {
            return 0.30; // Cold-start default
        }
        // Compute log returns
        let mut sum = 0.0;
        let mut sum_sq = 0.0;
        let count = (n - 1) as f64;
        for i in 1..n {
            let lr = (self.closes[i] / self.closes[i - 1]).ln();
            sum += lr;
            sum_sq += lr * lr;
        }
        let mean = sum / count;
        let variance = sum_sq / count - mean * mean;
        let bar_vol = variance.max(0.0).sqrt();
        // Annualize: σ_annual = σ_bar × √(bars_per_day × 252)
        bar_vol * (bars_per_day * 252.0).sqrt()
    }

    /// Amihud illiquidity ratio: mean(|return| / volume) over recent bars.
    /// Higher = less liquid. Returns 0.0 if insufficient data.
    pub fn amihud(&self) -> f64 {
        let n = self.closes.len();
        if n < 10 {
            return 0.0;
        }
        let mut sum = 0.0;
        let mut count = 0u32;
        // Use last 50 bars (or all available)
        let start = if n > 51 { n - 51 } else { 1 };
        for i in start..n {
            let vol = self.volumes[i];
            if vol == 0 {
                continue;
            }
            let abs_ret = ((self.closes[i] / self.closes[i - 1]) - 1.0).abs();
            sum += abs_ret / vol as f64;
            count += 1;
        }
        if count == 0 {
            0.0
        } else {
            sum / count as f64
        }
    }
}

/// SC-03: Line budget allocation across carry/active/scan pools.
/// Invariant: carry + active + scan <= 100.
pub struct LineBudget {
    pub carry: u32,
    pub active: u32,
    pub scan: u32,
}

impl LineBudget {
    pub fn new(carry: u32, active: u32, scan: u32) -> Option<Self> {
        if carry + active + scan <= 100 {
            Some(Self { carry, active, scan })
        } else {
            None
        }
    }

    pub fn total(&self) -> u32 {
        self.carry + self.active + self.scan
    }

    pub fn remaining(&self) -> u32 {
        100 - self.total()
    }
}

impl Default for LineBudget {
    fn default() -> Self {
        Self { carry: 30, active: 50, scan: 20 }
    }
}

/// The live/paper engine. Generic over BrokerAdapter for testability.
pub struct Engine<B: BrokerAdapter> {
    pub broker: B,
    pub portfolio: PortfolioState,
    pub arbiter: RiskArbiter,
    pub exit_engine: ExitEngine,
    pub wal: Option<WalWriter>,
    pub clock: Clock,
    pub config: EngineConfig,
    pub tracked_orders: Vec<String>,
    pub last_prices: HashMap<TickerId, f64>,
    pub positions: HashMap<TickerId, PositionState>,
    gap_cooldowns: HashMap<TickerId, u64>,
    /// P1-2.17: Tickers currently in exchange halt (no ticks for >30s + prev halted).
    halted_tickers: std::collections::HashSet<TickerId>,
    /// P1-2.15: Economic calendar events (FOMC, CPI, NFP, BOE) with blackout windows.
    pub economic_calendar: Vec<crate::config_loader::CalendarEvent>,
    pub now_ns: u64,
    pub startup_complete: bool,
    pub last_reconcile_ns: u64,
    /// Universe for tick routing and filtering (Phase 6A).
    pub universe: Universe,
    /// Rolling bar history per ticker for ATR calculation.
    pub bar_history: HashMap<TickerId, BarHistory>,
    /// P3-B: Per-ticker OHLCV snapshot buffers for Apex Scout (60-second windows).
    /// Used during MODE_A to accumulate candles before evaluating via ApexScout.
    pub apex_snapshots: HashMap<TickerId, VecDeque<serde_json::Value>>,
    /// Last snapshot timestamp per ticker (for 60s bucketing).
    pub last_snapshot_ns: HashMap<TickerId, u64>,
    /// P3-B: Current 60-second OHLCV candle per Apex ticker.
    pub apex_candles: HashMap<TickerId, ApexCandle>,
    /// Hourly state hash tracking (H85).
    pub last_state_hash_ns: u64,
    /// 5-minute heartbeat for status monitoring.
    pub last_heartbeat_ns: u64,
    /// Phase 11: Current trading mode (5-mode clock).
    pub current_mode: TradingMode,
    /// P2-C: Last trading date for daily reset detection.
    pub last_trading_date: Option<String>,
    /// P3-A: Per-ticker GARCH(1,1) inference for real-time vol forecasting.
    pub garch_registry: GarchRegistry,
    /// P3-B: HotScanner for volatility-momentum signal detection on Apex tickers.
    pub hot_scanner: HotScanner,
    /// P3-C: RotationScanner for sector rotation signal detection during Mode B.
    pub rotation_scanner: RotationScanner,
    /// P3-D: Executioner for order lifecycle management.
    pub executioner: Executioner,
    /// P3-D: SmartRouter for cost-based order routing (passthrough in Crucible).
    pub smart_router: SmartRouter,
    /// P3-E: SubscriptionManager for IBKR 100-line rotation (no-op in Crucible with 12 tickers).
    pub subscription_manager: SubscriptionManager,
    /// P4-A: Lock-free telemetry counters.
    pub telemetry: Telemetry,
    /// P4-B: Panic guard for thread safety.
    pub panic_guard: PanicGuard,
    /// P4-B: Circuit breaker for broker error rate.
    pub broker_circuit_breaker: CircuitBreaker,
    /// P4-B: Watchdog for tick liveness.
    pub tick_watchdog: Watchdog,
    /// P4-B: True if current HALT was caused by tick watchdog (vs liquidation/panic/manual).
    /// Used by tick recovery logic to auto-clear watchdog-only HALTs when data resumes.
    pub halt_from_watchdog: bool,
    /// SK-02: Timestamp (ns) when HALT regime was entered. Used for zombie halt timeout (30min).
    pub halt_started_ns: u64,
    /// P5-A: EVT on GARCH residuals for tail risk CVaR.
    pub evt_registry: EvtRegistry,
    /// P5-D: Hayashi-Yoshida async covariance estimator.
    pub hy_engine: HayashiYoshidaEngine,
    /// P6-A: FX rate table for multi-currency position valuation.
    pub fx_table: FxRateTable,
    /// P6-B: Exchange profile registry (15 European exchanges + FTT).
    pub exchange_registry: ExchangeRegistry,
    /// P6-C: ISA gate for tax-wrapper compliance checks.
    pub isa_gate: IsaGate,
    /// P6-D: Asian session manager (TSE, HKEX, ASX, SGX, KRX, NZX).
    pub asian_session: AsianSession,
    /// P6-E: European session manager (15 exchanges).
    pub european_session: EuropeanSession,
    /// P6-F: Cross-timezone intelligence (Asian close → European open gap).
    pub cross_timezone: CrossTimezoneEngine,
    /// P6-G: Overnight carry manager (frozen stops, state transitions).
    pub carry_manager: CarryManager,
    /// P9: Cross-asset macro regime detection (VIX/DXY/credit/Fear&Greed).
    pub macro_regime: CrossAssetMacro,
    /// P10: Multi-frame volatility per ticker.
    pub multiframe_vol: HashMap<TickerId, MultiFrameVol>,
    /// P11: Sector rotation heat tracking.
    pub sector_tracker: SectorHeatTracker,
    /// P12: Predictive scoring (IC + auto-lock after 5 consecutive losses).
    pub predictive_scorer: PredictiveScorer,
    /// P14: Quote imbalance / spoof detection.
    pub quote_imbalance: QuoteImbalanceDetector,
    /// P15: Split adjustment handler.
    pub split_handler: SplitHandler,
    /// P16: Liquidation cascade defense (ISA ceiling, DD flatten, H12 halt).
    pub liquidation_defense: LiquidationDefense,
    /// P17: Broker connection resilience monitoring.
    pub broker_health: BrokerHealthMonitor,
    /// P18: WAL compression and rotation (1M event threshold).
    pub wal_compressor: WalCompressor,
    /// P19: State checkpoint manager (hourly, FNV-1a hash).
    pub checkpoint_mgr: CheckpointManager,
    /// P21: Multi-session mode manager (Dark/ModeA/ModeB/Auction/Carry).
    pub session_mgr: SessionManager,
    /// P21: Last session mode (for detecting transitions).
    pub last_session_mode: SessionMode,
    /// P21: Market configuration (ticker sets for each mode).
    pub market_config: MarketConfig,
    /// P22: Latency profiling (6 pipeline stages).
    pub latency_profiler: LatencyProfiler,
    /// P5-B: Student-t Kalman filters per ticker for price smoothing.
    pub kalman_filters: HashMap<TickerId, StudentTKalmanFilter>,
    /// P5-C: Log-Thompson sampler for multi-ticker allocation.
    pub thompson_sampler: LogThompsonSampler,
    /// Phase 1C: Regime detector (jump-diffusion + Hurst).
    pub regime_detector: RegimeDetector,
    // last_regime_decision REMOVED — was written but never read (dead field)
    // early_runner_detectors REMOVED — never populated, VanguardSniper handles all entries
    /// Phase 1C: Kelly calculator for Rust-side sizing (fractional 0.25).
    pub kelly_calculator: KellyCalculator,
    /// Phase 2: Current market scheduler session (6-phase global clock).
    pub current_trading_session: TradingSession,
    /// Watchlist hot-reload: last modified time per watchlist file (epoch secs).
    pub watchlist_mtimes: HashMap<String, u64>,
    /// Last time we rotated subscriptions from watchlist (15-min cycle, nanoseconds).
    pub last_watchlist_rotation_ns: u64,
    /// Last time dark horse slots were rotated (80/20 architecture, nanoseconds).
    pub last_dark_horse_rotation_ns: u64,
    /// Monotonically incrementing counter for generating unique order IDs.
    pub order_counter: u64,
    /// P21-FX: Cached currency code per ticker (from broker contract map).
    /// Used to convert native-currency prices to GBP for mark-to-market.
    pub ticker_currencies: HashMap<TickerId, String>,
    /// Simulation mode: when true, trades are simulated internally instead of
    /// being submitted to the broker. The broker is still used for market data.
    /// This prevents any real order submission to IBKR.
    pub simulation_mode: bool,
    /// Simulated trades log: records all simulated fills for reporting/PDF.
    pub simulated_trades: Vec<SimulatedTrade>,
    /// P2-3.8: Nanosecond timestamp when broker disconnect was first detected.
    /// Reset to 0 on reconnect. Used for emergency halt escalation.
    pub broker_disconnect_ns: u64,
}

/// A simulated trade record for logging and daily PDF reporting.
#[derive(Clone, Debug)]
pub struct SimulatedTrade {
    pub order_id: String,
    pub ticker_id: TickerId,
    pub symbol: String,
    pub direction: Direction,
    pub qty: u32,
    pub fill_price_native: f64,
    pub fill_price_gbp: f64,
    pub currency: String,
    pub confidence: f64,
    pub kelly_fraction: f64,
    pub trade_value_gbp: f64,
    pub timestamp_ns: u64,
    pub strategy: String,
    pub status: SimTradeStatus,
    pub exit_price_gbp: Option<f64>,
    pub pnl_gbp: Option<f64>,
    pub exit_timestamp_ns: Option<u64>,
    /// Phase H: Indicator context captured at entry for Ouroboros learning.
    pub entry_rvol: f64,
    pub entry_hurst: f64,
    pub entry_adx: f64,
    /// N2b: Volume slope at entry (from Python bridge).
    pub entry_vol_slope: f64,
    /// N2b: VWAP distance % at entry (from Python bridge).
    pub entry_vwap_dist_pct: f64,
    /// N3a: Structural tradability score at entry (from Python bridge).
    pub entry_structural_score: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub enum SimTradeStatus {
    Open,
    Closed,
}

impl<B: BrokerAdapter> Engine<B> {
    pub fn new(broker: B, config: EngineConfig, wal: Option<WalWriter>, clock: Clock) -> Self {
        let equity = config.crucible.starting_equity_gbp;
        let is_simulation = config.crucible.paper_mode;
        let mut risk_config = config.risk.clone();
        if config.crucible.paper_mode {
            // N0b: Paper mode MUST match live economics for validation data to be meaningful.
            // Previous overrides (15 positions, 2.0% spread veto) created non-representative
            // data that showed 79% WR but would fail catastrophically in live due to costs.
            //
            // KEPT: max_positions override (from config) — allows broader data collection
            // but daily_trade_limit (CHECK 28) now caps ACTUAL entries per day.
            risk_config.max_positions = config.crucible.max_positions_override;
            // P0-1.4: Kelly ramp now INCREMENTED on each fill (paper + live).
            // Starts at 0, grows to 250. Half-Kelly applied until 250 trades validated.
            // Previously hardcoded to 250 which bypassed the ramp entirely.
            // risk_config.kelly_ramp_trades = 250;  // REMOVED — ramp must be earned
            // KEPT: Lower minimum entry for paper mode (bootstrap Kelly).
            risk_config.minimum_entry_gbp = 100.0;
            // N0b: Spread veto now MATCHES LIVE (0.3%) instead of 2.0%.
            // This ensures paper trades only enter at realistic spreads.
            // If 0.3% blocks too many LSE ETPs, the config value can be tuned —
            // but it must be the SAME in paper and live.
            // risk_config.spread_veto_pct = 2.0;  // REMOVED — use config value (0.3%)
        }
        // R6: Extract dividend withholding factor before risk_config is moved
        let dividend_factor = risk_config.dividend_withholding_factor;
        let arbiter = RiskArbiter::new(risk_config);
        // Q-051: Extract cost values before config is moved into Self
        let round_trip_fee = config.costs.round_trip_fee_pct;
        let ibkr_commission = config.costs.ibkr_commission_gbp;
        // Sprint 6: Extract hardening values before config is moved into Self
        let cb_errors: u64 = config.hardening.broker.circuit_breaker_errors.into();
        let cb_window = config.hardening.broker.circuit_breaker_window_secs;
        let cb_cooldown = config.hardening.broker.circuit_breaker_cooldown_secs;
        let watchdog_timeout = config.hardening.broker.tick_watchdog_timeout_secs;
        // P2-#27: Extract values before config is moved into Self
        let checkpoint_interval = config.reconciliation.checkpoint_interval_secs;
        // P2-B0.7: Extract macro defaults
        let macro_vix = config.macro_defaults.vix;
        let macro_dxy = config.macro_defaults.dxy;
        let macro_credit = config.macro_defaults.credit_spread_bps;
        let macro_fg = config.macro_defaults.fear_greed;
        // P2-#1/#31/#32: Extract liquidation defense values
        let ld_isa_limit = config.risk.isa_annual_limit_gbp;
        let ld_flatten_dd = config.risk.daily_drawdown_pct;
        let ld_halt_stops = config.risk.consecutive_loss_halt;
        Self {
            broker,
            portfolio: PortfolioState::with_dividend_factor(equity, dividend_factor),
            arbiter,
            exit_engine: {
                // Sprint 6: Construct Chandelier from config, not hardcoded defaults.
                let ch = &config.chandelier;
                let mut strategy = ChandelierStrategy::from_config(
                    &ch.rung_pct,
                    ch.initial_stop_atr_mult,
                    ch.rung3_trail_atr,
                    ch.rung4_trail_atr,
                    ch.rung5_trail_atr,
                    round_trip_fee,
                    ch.atr_floor_pct,
                );
                // Sprint G: Wire volume exhaustion config into Chandelier strategy.
                strategy.set_exhaustion_config(
                    ch.exhaustion.enabled,
                    ch.exhaustion.rvol_exhaustion_mult,
                    ch.exhaustion.tight_stop_atr,
                );
                let exit_config = ExitConfig {
                    price_spike_pct: ch.price_spike_pct,
                    dust_threshold_gbp: ch.dust_threshold_gbp,
                    time_stop_enabled: config.exit_time_stop.enabled,
                    time_stop_max_minutes_to_rung2: config.exit_time_stop.max_minutes_to_rung2,
                    time_stop_aggressive_trail_atr: config.exit_time_stop.aggressive_trail_atr,
                    ..ExitConfig::default()
                };
                ExitEngine::new(exit_config, Box::new(strategy))
            },
            wal,
            clock,
            config,
            tracked_orders: Vec::new(),
            last_prices: HashMap::new(),
            positions: HashMap::new(),
            gap_cooldowns: HashMap::new(),
            halted_tickers: std::collections::HashSet::new(),
            economic_calendar: Vec::new(),
            now_ns: 0,
            startup_complete: false,
            last_reconcile_ns: 0,
            universe: Universe::new(UniverseConfig::default()),
            bar_history: HashMap::new(),
            apex_snapshots: HashMap::new(),
            last_snapshot_ns: HashMap::new(),
            apex_candles: HashMap::new(),
            last_state_hash_ns: 0,
            last_heartbeat_ns: 0,
            current_mode: TradingMode::Dark,
            last_trading_date: None,
            garch_registry: GarchRegistry::empty(),
            hot_scanner: HotScanner::new(30.0, 10), // Score threshold 30, max 10 candidates
            rotation_scanner: RotationScanner::new(0.05, 10), // 5% rotation strength threshold, max 10 candidates
            executioner: Executioner::new(),
            smart_router: SmartRouter::with_costs(IsaGate::new("2026-04-06"), ibkr_commission),
            subscription_manager: SubscriptionManager::new(500), // Track up to 500 registered entries (active subset rotated per mode)
            telemetry: Telemetry::new(),
            panic_guard: PanicGuard::new(),
            broker_circuit_breaker: CircuitBreaker::new(cb_errors, cb_window, cb_cooldown),
            tick_watchdog: Watchdog::new(watchdog_timeout),
            halt_from_watchdog: false,
            halt_started_ns: 0,
            evt_registry: EvtRegistry::default(),
            hy_engine: HayashiYoshidaEngine::default(),
            fx_table: FxRateTable::default(),
            exchange_registry: ExchangeRegistry::default(),
            isa_gate: IsaGate::new("2026-04-06"),
            asian_session: AsianSession::default(),
            european_session: EuropeanSession::default(),
            cross_timezone: CrossTimezoneEngine::default(),
            carry_manager: CarryManager::default(),
            macro_regime: {
                // P2-B0.7: Initialize macro regime from config (fail-safe defaults).
                let indicator = crate::cross_asset_macro::MacroIndicator::from_config(
                    macro_vix, macro_dxy, macro_credit, macro_fg,
                );
                CrossAssetMacro::from_indicator(indicator)
            },
            multiframe_vol: HashMap::new(),
            sector_tracker: SectorHeatTracker::new(),
            predictive_scorer: PredictiveScorer::new(),
            quote_imbalance: QuoteImbalanceDetector::new(),
            split_handler: SplitHandler::new(),
            liquidation_defense: {
                let mut ld = LiquidationDefense::new(ld_isa_limit);
                ld.flatten_drawdown_pct = ld_flatten_dd;
                ld.halt_consecutive_stops = ld_halt_stops;
                ld
            },
            broker_health: BrokerHealthMonitor::new(),
            wal_compressor: WalCompressor::new("events/current.ndjson", "events/archive", 1_000_000),
            checkpoint_mgr: CheckpointManager::new(checkpoint_interval),
            session_mgr: SessionManager::new(),
            last_session_mode: SessionMode::Dark,
            market_config: MarketConfig::default(),
            latency_profiler: LatencyProfiler::new(),
            kalman_filters: HashMap::new(),
            thompson_sampler: LogThompsonSampler::new(),
            regime_detector: RegimeDetector::new(),
            // last_regime_decision removed (dead field)
            // early_runner_detectors removed (dead code)
            kelly_calculator: KellyCalculator::new(),
            current_trading_session: TradingSession::Closed,
            watchlist_mtimes: HashMap::new(),
            last_watchlist_rotation_ns: 0,
            last_dark_horse_rotation_ns: 0,
            order_counter: 0,
            ticker_currencies: HashMap::new(),
            simulation_mode: is_simulation,
            simulated_trades: Vec::new(),
            broker_disconnect_ns: 0,
        }
    }

    /// Execute the 8-step startup sequence.
    pub fn startup(
        &mut self,
        wal_events: &[crate::types::WalEvent],
        broker_time_secs: u64,
        system_time_ns: u64,
    ) -> Result<StartupResult, EngineError> {
        // Step 1: Verify broker connection (skip in simulation mode — broker is optional)
        let has_broker = self.broker.is_connected();
        if !has_broker && !self.simulation_mode {
            return Err(EngineError::BrokerNotConnected);
        }
        if !has_broker {
            eprintln!("STARTUP: No broker connection — simulation mode, skipping broker steps");
        }

        // Step 2: Sync clock (reqCurrentTime → offset)
        self.clock.sync(broker_time_secs, system_time_ns);
        let clock_offset = self.clock.offset_secs();
        eprintln!(
            "STARTUP: Clock synced, offset={clock_offset:.3}s (broker {}s)",
            broker_time_secs
        );

        // Step 3: Replay WAL → portfolio state + risk regime
        let replay_result =
            crate::wal_replay::replay_from_snapshot(wal_events, &mut self.portfolio);
        // FIX 2026-03-17: Recalculate equity from cash + open positions immediately
        // after WAL replay.  mark_to_market() needs live prices, but we don't have
        // ticks yet, so use entry prices as proxy (positions already carry avg_entry).
        // This ensures equity is correct from startup instead of stuck at £10,000.
        {
            let entry_prices: std::collections::HashMap<TickerId, f64> = self
                .portfolio
                .positions()
                .iter()
                .map(|(&tid, pos)| (tid, pos.avg_entry))
                .collect();
            self.portfolio.mark_to_market(&entry_prices);
            self.portfolio.update_high_water();
        }
        eprintln!(
            "STARTUP: WAL replayed {} events, {} orphans, equity=£{:.2} cash=£{:.2} positions={}",
            replay_result.events_replayed,
            replay_result.orphaned_orders.len(),
            self.portfolio.equity,
            self.portfolio.cash,
            self.portfolio.filled_count(),
        );

        // P0-1.4: Restore Kelly ramp counter from WAL.
        if replay_result.kelly_ramp_count > 0 {
            self.arbiter.config.kelly_ramp_trades = replay_result.kelly_ramp_count as u32;
            let ramp_pct = ((replay_result.kelly_ramp_count as f64 / self.arbiter.config.kelly_ramp_target as f64).clamp(0.1, 1.0) * 100.0) as u32;
            eprintln!(
                "STARTUP: Kelly ramp restored from WAL: {} trades ({}% Kelly)",
                replay_result.kelly_ramp_count, ramp_pct,
            );
        }

        // FIX 2026-03-11: Restore risk regime from WAL (prevents Halt bypass on restart).
        if let Some(ref regime_str) = replay_result.restored_regime {
            let restored = match regime_str.as_str() {
                "Halt" => RiskRegime::Halt,
                "Flatten" => RiskRegime::Flatten,
                "Reduce" => RiskRegime::Reduce,
                _ => RiskRegime::Normal,
            };
            if restored > RiskRegime::Normal {
                self.arbiter.regime = restored;
                // If Halt was from tick watchdog, enable auto-recovery when ticks resume.
                if restored == RiskRegime::Halt {
                    if let Some(ref trigger) = replay_result.restored_regime_trigger {
                        if trigger == "tick_watchdog_expired" {
                            self.halt_from_watchdog = true;
                            eprintln!(
                                "STARTUP: Restored risk regime from WAL: Halt (watchdog-sourced, auto-recovery enabled)"
                            );
                        } else {
                            eprintln!(
                                "STARTUP: Restored risk regime from WAL: Halt (trigger={trigger}, manual recovery required)"
                            );
                        }
                    } else {
                        eprintln!(
                            "STARTUP: Restored risk regime from WAL: Halt (unknown trigger, safety brake preserved)"
                        );
                    }
                } else {
                    eprintln!(
                        "STARTUP: Restored risk regime from WAL: {:?} (safety brake preserved)",
                        restored
                    );
                }
            }
        }

        // In simulation mode, always start with Normal regime (no stale WAL state).
        if self.simulation_mode && self.arbiter.regime > RiskRegime::Normal {
            eprintln!(
                "STARTUP: Simulation mode — overriding WAL regime {:?} → Normal",
                self.arbiter.regime
            );
            self.arbiter.regime = RiskRegime::Normal;
        }

        // Step 4: Reconcile positions with broker (skip if no broker)
        let (_recon_mismatches, recon_matches) = if has_broker {
            let broker_positions = self.broker.request_positions()?;
            let recon = reconciler::reconcile_positions(&self.portfolio, &broker_positions);
            if !recon.is_clean {
                eprintln!(
                    "CRITICAL: {} position mismatches detected!",
                    recon.mismatches.len()
                );
                // Trust broker, trigger FLATTEN (H130)
                self.arbiter.regime = RiskRegime::Flatten;
            }
            (recon.mismatches.len(), recon.matches)
        } else {
            eprintln!("STARTUP: Step 4 skipped (no broker — simulation mode)");
            (0, 0)
        };

        // Step 5: Resolve orphaned orders (skip if no broker)
        let orphans = if has_broker {
            let broker_orders = self.broker.request_open_orders()?;
            let orphans = reconciler::detect_orphaned_orders(&self.tracked_orders, &broker_orders);
            if !orphans.is_empty() {
                eprintln!(
                    "WARNING: {} orphaned orders found: {:?}",
                    orphans.len(),
                    orphans
                );
                // Cancel orphans
                for oid in &orphans {
                    let _ = self.broker.cancel_order(oid);
                }
            }
            orphans
        } else {
            eprintln!("STARTUP: Step 5 skipped (no broker — simulation mode)");
            vec![]
        };

        // Step 6: Register tickers from config into universe
        // Load universe classification from Ouroboros nightly artifacts (Tier 3 = Apex)
        use crate::ouroboros_loader::load_universe_classification;
        let uc = load_universe_classification(std::path::Path::new("config"));
        let tier3_set: std::collections::HashSet<i64> = uc.tier3.iter().copied().collect();

        for (idx, ticker) in self.config.tickers.iter().enumerate() {
            // Determine classification: if ticker ID is in tier3, it's Apex; else Vanguard
            let ticker_id = TickerId(idx as u32);
            let class = if tier3_set.contains(&(idx as i64)) {
                UniverseClass::Apex
            } else {
                UniverseClass::Vanguard
            };
            self.universe.register(&ticker.symbol, class);
            self.bar_history
                .insert(ticker_id, BarHistory::new(500));
            // P5-C: Register ticker in Thompson sampler for allocation learning.
            self.thompson_sampler.register(ticker_id);
            eprintln!(
                "STARTUP: registered ticker={} symbol={} class={:?}",
                idx, ticker.symbol, class
            );
        }
        let tickers_registered = self.config.tickers.len();

        // P0-1.8: Restore bar_history from disk (overwrites empty histories with saved bars).
        self.restore_bar_history();

        // FIX 5: Mark FX rates as fresh at startup (hardcoded defaults are close enough
        // for paper trading). Prevents stale warning every reconcile cycle.
        self.fx_table.last_update_ns = system_time_ns;
        eprintln!("STARTUP: FX rates initialized (defaults, marked fresh)");

        // Step 7: Write SystemReady to WAL
        self.now_ns = system_time_ns;
        // Set initial trading mode from system clock — don't wait for first tick.
        // Without this, engine starts in Dark mode even during market hours.
        let startup_london_secs = self.clock.now_london_secs(system_time_ns);
        self.current_mode = TradingMode::from_london_secs(startup_london_secs);
        eprintln!(
            "STARTUP: Initial trading mode = {:?} (London time {}:{:02})",
            self.current_mode,
            startup_london_secs / 3600,
            (startup_london_secs % 3600) / 60,
        );
        self.write_wal(WalPayload::SystemReady {
            wal_events_replayed: replay_result.events_replayed,
            positions_reconciled: recon_matches as u32,
        });

        // Step 8: Mark startup complete
        self.startup_complete = true;
        eprintln!(
            "ENGINE_MODE: {} — {}",
            if self.simulation_mode { "SIMULATION (no real orders)" } else { "LIVE TRADING" },
            if self.simulation_mode { "Using IBKR for market data only. All trades are simulated internally." }
            else { "Orders will be submitted to IBKR." },
        );
        eprintln!(
            "RISK_CONFIG: max_positions={} confidence_floor={:.0} cash_buffer_pct={:.1} spread_veto_pct={:.1} min_entry_gbp={:.0} simulation={}",
            self.arbiter.config.max_positions, self.arbiter.config.confidence_floor,
            self.arbiter.config.cash_buffer_pct, self.arbiter.config.spread_veto_pct,
            self.arbiter.config.minimum_entry_gbp, self.simulation_mode,
        );
        eprintln!(
            "EXIT_CONFIG: time_stop_enabled={} max_minutes_to_rung2={} aggressive_trail_atr={:.2}",
            self.exit_engine.config.time_stop_enabled,
            self.exit_engine.config.time_stop_max_minutes_to_rung2,
            self.exit_engine.config.time_stop_aggressive_trail_atr,
        );
        eprintln!(
            "Q-051 COST_CONFIG: round_trip_fee={:.3}%, commission=GBP{:.2}, fx_cost={:.3}%",
            self.config.costs.round_trip_fee_pct * 100.0,
            self.config.costs.ibkr_commission_gbp,
            self.config.costs.fx_conversion_pct * 100.0,
        );
        self.last_reconcile_ns = system_time_ns;
        self.last_state_hash_ns = system_time_ns;

        Ok(StartupResult {
            wal_events_replayed: replay_result.events_replayed,
            positions_reconciled: recon_matches as u32,
            orphans_found: orphans.len(),
            clock_offset_secs: clock_offset,
            tickers_registered,
        })
    }

    /// Get current London time (seconds from midnight).
    fn london_time_secs(&self) -> u32 {
        self.clock.now_london_secs(self.now_ns)
    }

    /// Update bar history for a ticker from high/low data.
    pub fn update_bar_data(&mut self, ticker_id: TickerId, high: f64, low: f64, close: f64, volume: u64) {
        let history = self
            .bar_history
            .entry(ticker_id)
            .or_insert_with(|| BarHistory::new(500));
        history.push(high, low, close, volume);
    }

    /// Get current ATR for a ticker (Wilder's 14-period).
    pub fn current_atr(&self, ticker_id: TickerId) -> f64 {
        self.bar_history
            .get(&ticker_id)
            .map(|h| h.atr(14))
            .unwrap_or_else(|| {
                // Fallback: 2% of last price
                self.last_prices.get(&ticker_id).copied().unwrap_or(1.0) * 0.02
            })
    }

    /// Route a tick through Universe filters (Phase 6A).
    /// Returns None if tick was filtered, Some(MarketTick) if it passed.
    pub fn route_tick(&mut self, tick: &MarketTick) -> Option<RouteResult> {
        if !self.universe.tickers.contains_key(&tick.ticker_id) {
            // Ticker not registered in universe — pass through as Vanguard
            return Some(RouteResult::Vanguard(tick.clone()));
        }
        Some(self.universe.route_tick(tick, self.now_ns))
    }

    /// Process a single tick through the full pipeline (test path only).
    /// No signal = no entry — exits still evaluated for existing positions.
    pub fn process_tick(&mut self, tick: MarketTick) {
        self.process_tick_with_signal(tick, None);
    }

    /// Process a tick with an optional signal from the Python Brain.
    /// If signal is None, returns immediately — no phantom trades generated.
    pub fn process_tick_with_signal(&mut self, tick: MarketTick, signal: Option<BrainSignal>) {
        if !self.startup_complete {
            return;
        }
        // P4-A: Record tick in telemetry
        self.telemetry.ticks_received.inc();
        // P4-B: Feed watchdog on every tick
        self.tick_watchdog.feed(tick.timestamp_ns);

        // P4-C: Auto-recover from watchdog-induced HALT when ticks resume.
        // Only clear HALT if it was caused by tick watchdog expiry (not by liquidation
        // defense, panic guard, H12, or broker disconnect). The halt_from_watchdog flag
        // is set exclusively by the watchdog expiry path in verify_hardening_invariants().
        if self.arbiter.regime == RiskRegime::Halt && self.halt_from_watchdog {
            eprintln!("WATCHDOG_RECOVERY: Ticks resumed — Halt -> Normal (watchdog-only)");
            self.arbiter.regime = RiskRegime::Normal;
            self.halt_from_watchdog = false;
            self.write_wal(WalPayload::RiskStateChange {
                from: "Halt".to_string(),
                to: "Normal".to_string(),
                trigger: "tick_watchdog_recovery".to_string(),
            });
        }

        let t2t_start_ns = self.now_ns; // For T2T latency logging (H118)
        self.now_ns = tick.timestamp_ns;

        // P21-FX: Convert tick prices from native currency to GBP.
        // Cache the ticker's currency on first encounter.
        let tid = tick.ticker_id;
        let currency_code = self.ticker_currencies.entry(tid)
            .or_insert_with(|| self.broker.currency_for_ticker(&tid).to_string())
            .clone();
        let fx_currency = crate::currency::Currency::from_str_code(&currency_code);
        // Keep native prices for order submission (IBKR needs native-currency limit price)
        // WIRED (Sprint 3A): Both bid and ask kept for spread calculation.
        let native_ask = tick.ask;
        let native_bid = tick.bid;
        // Compute native spread for tradability assessment
        let native_mid = (native_ask + native_bid) / 2.0;
        let native_spread_bps = if native_mid > 0.0 {
            (native_ask - native_bid) / native_mid * 10_000.0
        } else {
            0.0
        };
        // Convert tick to GBP for all internal calculations
        let mut tick = tick;
        if let Some(cur) = fx_currency {
            if cur != crate::currency::Currency::GBP {
                tick.bid = self.fx_table.to_gbp(tick.bid, cur);
                tick.ask = self.fx_table.to_gbp(tick.ask, cur);
                tick.last = self.fx_table.to_gbp(tick.last, cur);
            }
        }
        // AUDIT-FIX (2026-03-18): GBX→GBP conversion for LSE instruments.
        // IBKR sends LSE ETP prices in GBX (pence), not GBP (pounds).
        // Detect: contract currency GBP + exchange LSEETF/LSE + price > 500.
        // No LSE leveraged ETP is legitimately >£500/share (they range £1-200).
        // 3SEM.L at 9894 GBX = £98.94 GBP. QQQ3.L at 3250 GBX = £32.50 GBP.
        {
            let exchange = self.broker.exchange_for_ticker(&tid);
            let is_lse = matches!(exchange, "LSEETF" | "LSE");
            let is_gbp = currency_code == "GBP";
            if is_lse && is_gbp && tick.ask > self.config.hardening.sizing.gbx_threshold {
                tick.bid /= 100.0;
                tick.ask /= 100.0;
                tick.last /= 100.0;
            }
        }

        // Gap detection (H66): >2% gap → 15-min cooldown
        if let Some(&prev) = self.last_prices.get(&tid)
            && prev > 0.0
        {
            let gap_pct = ((tick.last - prev) / prev).abs();
            if gap_pct > 0.02 {
                let cooldown_ns = self.config.gap_cooldown_mins as u64 * 60 * 1_000_000_000;
                self.gap_cooldowns.insert(tid, self.now_ns + cooldown_ns);
            }
        }
        // P1-2.17: Post-halt price discovery lagger.
        // If ticker was in halt set and we receive a tick, it's resuming.
        // Set 5-minute cooldown to avoid wild price-discovery volatility.
        if self.halted_tickers.remove(&tid) {
            self.gap_cooldowns.insert(tid, self.now_ns + HALT_COOLDOWN_NS);
            eprintln!("HALT_LIFT: ticker={} resumed trading — 5-min cooldown set", tid.0);
            // Reset time-stop counter for any open position in this ticker.
            // Post-unhalt price discovery is noise (Hasbrouck) — the bid-ask spread
            // hasn't normalized yet. Give the position a fresh 45-minute window
            // so the aggressive 0.3x ATR time-stop doesn't fire on auction jitter.
            if let Some(pos) = self.positions.get_mut(&tid) {
                let old_ticks = pos.active_trading_ticks;
                pos.active_trading_ticks = 0;
                eprintln!(
                    "HALT_GRACE: ticker={} active_trading_ticks reset {} → 0 (post-unhalt grace)",
                    tid.0, old_ticks,
                );
            }
        }

        // P3-A: GARCH(1,1) update with log return
        // P5-A: Feed standardized residuals to EVT for tail risk
        let garch_sigma = if let Some(&prev) = self.last_prices.get(&tid)
            && prev > 0.0
            && tick.last > 0.0
        {
            let log_return = (tick.last / prev).ln();
            if let Some(std_residual) = self.garch_registry.update_residual(tid, log_return) {
                self.evt_registry.add_residual(tid, std_residual);
            }
            self.garch_registry.sigma(tid).unwrap_or(0.30)
        } else {
            0.30 // Cold-start default
        };

        // WIRED (Sprint 2B): Query EVT CVaR for tail risk sizing.
        // FAIL-CLOSED: if no CVaR available, assume 20% reduction (unknown tail risk = conservative).
        let evt_cvar = self.evt_registry.cvar(tid).unwrap_or(0.0);

        // P14: Quote imbalance — record bid/ask and check for spoofing.
        if tick.bid > 0.0 && tick.ask > 0.0 {
            self.quote_imbalance.record_quote(tid, tick.bid, tick.ask, self.now_ns);
            if self.quote_imbalance.is_spoofed(tid) {
                let drops = self.quote_imbalance.drop_count(tid);
                eprintln!("SPOOF_DETECT: ticker={} drops={} — dropping tick, escalating to REDUCE", tid.0, drops);
                // WIRED (Sprint 3B): Log anomaly when drop count spikes
                if drops >= 3 {
                    self.telemetry.record_veto("SpoofAnomaly");
                }
                if self.arbiter.regime < RiskRegime::Reduce {
                    self.arbiter.regime = RiskRegime::Reduce;
                }
                return;
            }
        }

        // P10: Multi-frame vol — feed 5-second log return.
        if let Some(&prev) = self.last_prices.get(&tid)
            && prev > 0.0
            && tick.last > 0.0
        {
            let log_return = (tick.last / prev).ln();
            self.multiframe_vol
                .entry(tid)
                .or_default()
                .record_return(TimeFrame::FiveMinute, log_return, self.now_ns);
        }

        // P5-B: Student-t Kalman filter for price smoothing.
        // WIRED (Sprint 2A): Kalman state used for price divergence signals.
        let kalman_state = {
            let filter = self.kalman_filters
                .entry(tid)
                .or_insert_with(|| StudentTKalmanFilter::new(tick.last, 1.0, 0.01, 0.1, 100));
            filter.step(tick.last);
            filter.state()
        };
        // Store smoothed price for downstream signal quality assessment.
        // state() returns f64 directly — the smoothed price estimate.
        // Divergence = (raw - smoothed) / smoothed. Positive = raw leads (potential breakout).
        let kalman_smoothed = kalman_state;
        let kalman_divergence = if kalman_smoothed > 0.0 {
            (tick.last - kalman_smoothed) / kalman_smoothed
        } else {
            0.0
        };

        // P5-D: Record tick for Hayashi-Yoshida covariance estimation
        self.hy_engine.record_tick(tid, tick.last, tick.timestamp_ns);

        // ATR from rolling bar history (Wilder's 14-period)
        let atr = self.current_atr(tid);

        // Phase 1C: Regime detection (jump-diffusion + Hurst) on each tick.
        // FIX: Calculate price_move BEFORE updating last_prices (was always 0.0).
        let regime_decision = {
            let rvol_val = self.bar_history.get(&tid)
                .map(|h| h.realized_vol(6120.0))
                .unwrap_or(0.30);
            let price_move = if let Some(&prev) = self.last_prices.get(&tid)
                && prev > 0.0
            {
                (tick.last - prev).abs()
            } else {
                0.0
            };
            let prices_for_hurst: Vec<f64> = self.bar_history.get(&tid)
                .map(|h| h.closes.iter().copied().collect())
                .unwrap_or_default();
            self.regime_detector.evaluate(rvol_val, atr, price_move, &prices_for_hurst)
        };
        // Dead write removed: last_regime_decision was never read
        // self.last_regime_decision.insert(tid, regime_decision);

        // Update last_prices AFTER price_move calculation (FIX for Issue #14)
        self.last_prices.insert(tid, tick.last);

        // Real London time from synced clock
        let time_secs = self.london_time_secs();

        // Phase 11: Determine current trading mode
        self.current_mode = TradingMode::from_london_secs(time_secs);

        // ──────────────────────────────────────────────────────────────────
        // EXIT EVALUATION — runs BEFORE Dark/Closed gates so positions can
        // ALWAYS exit when price data arrives, regardless of session state.
        // Without this, positions opened during a session would become
        // permanently stuck when the session ends (TradingSession::Closed)
        // or during the Dark maintenance window (21:00-23:00 London).
        // ──────────────────────────────────────────────────────────────────
        if let Some(pos) = self.positions.get_mut(&tid) {
            // P4-C: Update InfiniteChandelier adaptive multipliers before exit evaluation.
            if self.exit_engine.is_infinite_chandelier() {
                let realized_vol = self
                    .bar_history
                    .get(&tid)
                    .map(|h| h.realized_vol(6120.0))
                    .unwrap_or(0.30);
                let time_fraction = Clock::time_of_day_fraction(time_secs);
                let momentum = if let Some(&prev) = self.last_prices.get(&tid)
                    && prev > 0.0
                {
                    (tick.last - prev) / prev
                } else {
                    0.0
                };
                let amihud = self
                    .bar_history
                    .get(&tid)
                    .map(|h| h.amihud())
                    .unwrap_or(0.0);
                let heat = self.portfolio.portfolio_heat_pct();
                let is_reduce = self.arbiter.regime >= RiskRegime::Reduce;
                self.exit_engine.strategy_mut().update_multipliers(
                    realized_vol, time_fraction, momentum, amihud, heat, is_reduce,
                );
            }

            if !self.exit_engine.is_price_spike(pos.highest_high, tick.last, tick.bid, tick.ask) {
                if let Some((old_rung, new_rung)) = self.exit_engine.update_tracking(pos, tick.last, atr) {
                    // Persist rung advance to WAL for crash recovery
                    if let Some(ref mut wal) = self.wal {
                        let evt = make_wal_event(self.now_ns, WalPayload::RungAdvanced {
                            ticker_id: tid.0,
                            order_id: pos.origin_order_id.clone(),
                            old_rung,
                            new_rung,
                            stop_price: pos.stop_price,
                            highest_high: pos.highest_high,
                        });
                        if let Err(e) = wal.append(&evt) {
                            eprintln!("WAL_WRITE_FAIL: rung advance: {} — will HALT next tick", e);
                            // Can't borrow self.arbiter here; write_wal() handles HALT for other paths
                        }
                    }
                }
                // P1-2.6: Activate mega-runner when profit exceeds 3 ATR.
                if atr > 0.0 {
                    let profit_atr = (tick.last - pos.avg_entry) / atr;
                    self.exit_engine.strategy_mut().update_mega_runner(profit_atr);
                }

                // Sprint G: Volume exhaustion — tighten stop when RVOL signals climactic reversal.
                // RVOL is already relative volume (current/average), so RVOL >= 10 means 10x normal.
                // If triggered, ratchet stop UP to (highest_high - tight_atr * ATR).
                if atr > 0.0 {
                    let current_rvol = self.bar_history.get(&tid)
                        .map(|h| h.realized_vol(6120.0))
                        .unwrap_or(0.0);
                    if let Some(tight_atr) = self.exit_engine.check_exhaustion(current_rvol) {
                        let exhaustion_stop = pos.highest_high - tight_atr * atr;
                        // H68: stop ratchet — can NEVER decrease
                        if exhaustion_stop > pos.stop_price {
                            eprintln!(
                                "EXHAUSTION: ticker={} rvol={:.1} tightening stop {:.4} → {:.4} (tight_atr={:.2})",
                                tid.0, current_rvol, pos.stop_price, exhaustion_stop, tight_atr
                            );
                            pos.stop_price = exhaustion_stop;
                        }
                    }
                }

                pos.unrealized_pnl = (tick.last - pos.avg_entry) * pos.qty as f64;

                // Update MAE/MFE (Maximum Adverse/Favorable Excursion)
                if pos.unrealized_pnl < pos.mae {
                    pos.mae = pos.unrealized_pnl;
                }
                if pos.unrealized_pnl > pos.mfe {
                    pos.mfe = pos.unrealized_pnl;
                }

                let is_halt = self.arbiter.regime >= RiskRegime::Flatten;
                let is_eod = Clock::eod_phase(time_secs).is_some();
                let is_carried = self.carry_manager.is_carried(&tid);
                let exit_result = self
                    .exit_engine
                    .evaluate(pos, tick.last, atr, time_secs, is_halt, is_eod, is_carried);
                if let Some(ref result) = exit_result {
                    let reason_str = format!("{:?}", result.signal.reason);
                    let priority_str = format!("{:?}", result.signal.priority);
                    let final_pnl = pos.unrealized_pnl - pos.total_commission;
                    let entry_time = pos.entry_timestamp_ns;
                    let exit_qty = pos.qty;
                    let highest_rung = pos.trailing_rung;
                    let entry_price_gbp = pos.avg_entry;
                    let exit_price_gbp = tick.last;
                    let pos_mae = pos.mae;
                    let pos_mfe = pos.mfe;
                    let pos_entry_type = pos.entry_type.clone();
                    // N0e: Capture cost fields for PositionClosed event.
                    let gross_pnl = pos.unrealized_pnl;  // Before commission
                    let total_commission = pos.total_commission;
                    let spread_at_entry = pos.spread_at_entry_pct;
                    let daily_trade_num = pos.daily_trade_number;
                    // N0e: Current spread at exit time
                    let spread_at_exit = if tick.bid > 0.0 {
                        (tick.ask - tick.bid) / tick.bid * 100.0
                    } else {
                        0.0
                    };
                    self.write_wal(WalPayload::ExitSignal {
                        ticker_id: tid.0,
                        reason: reason_str.clone(),
                        priority: priority_str,
                    });

                    let exit_order_id = format!("exit-{}", uuid::Uuid::now_v7());
                    let sell_limit = match result.signal.order_type {
                        crate::types::ExitOrderType::LimitAtStop => {
                            let raw = result.signal.limit_price.unwrap_or(tick.bid);
                            round_to_tick_size(raw, &self.config)
                        }
                        crate::types::ExitOrderType::MarketSell
                        | crate::types::ExitOrderType::MarketToLimit => {
                            let raw = tick.bid * 0.999;
                            round_to_tick_size(raw.max(0.01), &self.config)
                        }
                    };

                    let exit_symbol = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
                    let exit_currency = self.ticker_currencies.get(&tid).cloned().unwrap_or_else(|| "GBP".to_string());
                    self.write_wal(WalPayload::RoutedOrder {
                        order_id: exit_order_id.clone(),
                        ticker_id: tid.0,
                        side: "Sell".to_string(),
                        confidence: 0.0,
                        strategy: reason_str.clone(),
                        kelly_fraction: 0.0,
                        approved_size: sell_limit * exit_qty as f64,
                        symbol: exit_symbol,
                        qty: exit_qty,
                        currency: exit_currency,
                        entry_rvol: 0.0,
                        entry_hurst: 0.0,
                        entry_adx: 0.0,
                        rsi: 0.0,
                        vwap_dist_pct: 0.0,
                        atr: 0.0,
                        vol_slope: 0.0,
                        spread_pct: 0.0,
                        mtf_score: 0.0,
                        entry_type: String::new(),
                        ibs: 0.0,
                    });
                    self.tracked_orders.push(exit_order_id.clone());

                    self.executioner.track_order(TrackedOrder {
                        order_id: exit_order_id.clone(),
                        ticker_id: tid,
                        lifecycle: OrderLifecycle::Submitted,
                        submit_ns: self.now_ns,
                        last_update_ns: self.now_ns,
                        qty: exit_qty,
                        filled_qty: 0,
                        limit_price: sell_limit,
                        retries: 0,
                        is_exit: true,
                    });

                    if !self.simulation_mode {
                        if let Err(e) = self.broker.submit_order(
                            &exit_order_id, tid, OrderSide::Sell, exit_qty, sell_limit,
                        ) {
                            eprintln!(
                                "EXIT: sell order FAILED for ticker={} qty={} err={e} — position remains!",
                                tid.0, exit_qty
                            );
                            return;
                        }
                        let sell_events = self.broker.drain_events();
                        for ev in &sell_events {
                            self.process_broker_event(ev);
                        }
                    }

                    eprintln!(
                        "EXIT: {:?} ticker={} qty={} limit={sell_limit:.4} pnl=£{final_pnl:.2} rung={highest_rung} {}",
                        result.signal.reason, tid.0, exit_qty,
                        if self.simulation_mode { "[SIMULATED]" } else { "[LIVE]" },
                    );

                    let close_symbol = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
                    let regime_at_entry = format!("{:?}", self.arbiter.regime);
                    let exit_exchange = self.broker.exchange_for_ticker(&tid).to_string();
                    // Look up ALL entry context from the SimulatedTrade that opened this position
                    let entry_ctx = self.simulated_trades.iter()
                        .rfind(|t| t.ticker_id == tid && t.status == SimTradeStatus::Open);
                    let entry_confidence = entry_ctx.map(|t| t.confidence).unwrap_or(0.0);
                    let entry_strategy = entry_ctx.map(|t| t.strategy.clone()).unwrap_or_else(|| "unknown".to_string());
                    let e_rvol = entry_ctx.map(|t| t.entry_rvol).unwrap_or(0.0);
                    let e_hurst = entry_ctx.map(|t| t.entry_hurst).unwrap_or(0.0);
                    let e_adx = entry_ctx.map(|t| t.entry_adx).unwrap_or(0.0);
                    let e_vol_slope = entry_ctx.map(|t| t.entry_vol_slope).unwrap_or(0.0);
                    let e_vwap_dist = entry_ctx.map(|t| t.entry_vwap_dist_pct).unwrap_or(0.0);
                    // N2b: Compute enriched fields for trade taxonomy classification.
                    let hold_time_mins = if self.now_ns > entry_time {
                        ((self.now_ns - entry_time) / 60_000_000_000) as u32
                    } else {
                        0
                    };
                    // Classify session phase from London time (approximate from entry time).
                    let entry_session_phase = {
                        let london_secs = self.clock.now_london_secs(entry_time);
                        if london_secs < 8 * 3600 { "pre_open" }
                        else if london_secs < 10 * 3600 { "open" }
                        else if london_secs < 13 * 3600 { "morning" }
                        else if london_secs < 16 * 3600 { "afternoon" }
                        else { "close" }
                    }.to_string();
                    self.write_wal(WalPayload::PositionClosed {
                        ticker_id: tid.0,
                        final_pnl,
                        entry_time_ns: entry_time,
                        exit_time_ns: self.now_ns,
                        gross_pnl,
                        total_commission,
                        spread_at_entry_pct: spread_at_entry,
                        spread_at_exit_pct: spread_at_exit,
                        daily_trade_number: daily_trade_num,
                        symbol: close_symbol,
                        qty: exit_qty,
                        regime_at_entry,
                        confidence: entry_confidence,
                        highest_rung,
                        strategy: entry_strategy,
                        exchange: exit_exchange,
                        entry_price: entry_price_gbp,
                        exit_price: exit_price_gbp,
                        entry_rvol: e_rvol,
                        entry_hurst: e_hurst,
                        entry_adx: e_adx,
                        mae: pos_mae,
                        mfe: pos_mfe,
                        // N2b: Enriched fields for trade taxonomy.
                        hold_time_mins,
                        entry_session_phase,
                        vwap_dist_at_entry_pct: e_vwap_dist,
                        atr_pct_at_entry: if entry_price_gbp > 0.0 { atr / entry_price_gbp * 100.0 } else { 0.0 },
                        vix_at_entry: self.macro_regime.indicator().vix,
                        vol_slope_at_entry: e_vol_slope,
                        trade_class: String::new(),   // Assigned by nightly trade taxonomy
                        entry_type: pos_entry_type,
                    });
                    self.sector_tracker.clear_position(tid, tick.last * exit_qty as f64);
                    self.predictive_scorer.record_trade(tid, final_pnl, time_secs);
                    if final_pnl < 0.0 {
                        self.liquidation_defense.record_stop_loss();
                    } else {
                        self.liquidation_defense.record_win();
                    }
                    let close_notional = tick.last * exit_qty as f64;
                    if close_notional > 0.0 {
                        let return_pct = final_pnl / close_notional * 100.0;
                        self.thompson_sampler.observe(tid, return_pct);
                    }
                    self.portfolio.remove_position(tid);
                    self.positions.remove(&tid);
                    // AUDIT-FIX: Post-exit cooldown — prevent immediate re-entry spam.
                    // After a stop-out, wait EXIT_COOLDOWN_NS before re-entering same ticker.
                    // Reuses gap_cooldowns HashMap (already per-ticker + checked before signal).
                    self.gap_cooldowns.insert(tid, self.now_ns + EXIT_COOLDOWN_NS);

                    if self.simulation_mode {
                        let exit_gbp = tick.bid;
                        let now_ns = self.now_ns;
                        for st in self.simulated_trades.iter_mut() {
                            if st.ticker_id == tid && st.status == SimTradeStatus::Open {
                                st.status = SimTradeStatus::Closed;
                                st.exit_price_gbp = Some(exit_gbp);
                                st.pnl_gbp = Some(final_pnl);
                                st.exit_timestamp_ns = Some(now_ns);
                                break;
                            }
                        }
                        let sym = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
                        eprintln!(
                            "SIM_EXIT: {} x{} @ {:.4} GBP pnl=£{:.2} rung={highest_rung}",
                            sym, exit_qty, exit_gbp, final_pnl,
                        );
                    }
                    return; // Position closed — done processing this tick
                }
            }
        }

        // Phase 11: In Dark mode, no new entries.
        // ModeC (17:00-20:00 London) allows US-session trading.
        // NOTE: Exits are processed ABOVE this gate — only entries are blocked.
        if matches!(self.current_mode, TradingMode::Dark) {
            return;
        }

        // Phase 2: Market scheduler gate — skip new entries if global session is Closed.
        // NOTE: Exits are processed ABOVE this gate — only entries are blocked.
        if self.current_trading_session == TradingSession::Closed {
            return;
        }

        // APEX_TICK: Route Apex tickers to HotScanner during Mode A
        if matches!(self.current_mode, TradingMode::ModeA) && self.universe.is_apex(tid) {
            eprintln!("APEX_TICK: ticker={}, price={:.4}, volume={}", tid.0, tick.last, tick.volume);

            // Process through HotScanner for volatility-momentum scoring
            if let Some(candidate) = self.process_apex_tick(&tick) {
                eprintln!(
                    "APEX_SCORE_HIGH: ticker={}, score={:.1} → buffering for evaluation",
                    tid.0, candidate.score
                );
                // Score > threshold (30): buffer the tick for 60s snapshot evaluation

                // Update OHLCV candle for 60s aggregation
                let candle = self.apex_candles.entry(tid).or_insert_with(|| {
                    ApexCandle::new(tick.last, tick.volume, self.now_ns)
                });

                if candle.is_complete(self.now_ns) {
                    // 60s candle complete: send snapshot JSON to Python Brain
                    let snapshot_json = serde_json::json!({
                        "type": "apex_snapshot",
                        "ticker_id": tid.0,
                        "snapshots": [{
                            "timestamp_ns": candle.end_ns,
                            "open": candle.open,
                            "high": candle.high,
                            "low": candle.low,
                            "close": candle.close,
                            "volume": candle.volume,
                        }]
                    });

                    // Buffer for Python Brain evaluation
                    self.apex_snapshots.entry(tid).or_default().push_back(snapshot_json);
                    eprintln!("APEX_SEND: ticker={}, snapshot queued for Python Brain", tid.0);

                    // Start new candle
                    *candle = ApexCandle::new(tick.last, tick.volume, self.now_ns);
                } else {
                    // Update current candle
                    candle.update(tick.last, tick.volume);
                }
            }
            // Apex tickers only get Apex processing in Mode A, don't fallthrough to Vanguard logic
            return;
        }

        // P3-3.2 AUDIT: RotationScanner — instantiated but NEVER CALLED (dead code).
        // HotScanner — called in process_apex_tick() but output score discarded (logged only).
        // RegimeDetector — called at line ~1004, only has_jump used; hurst_regime+confidence unused.
        // scanner_score — wired from sig.confidence in EvalContext (item 3.4).
        // TODO(P3+): Wire HotScanner score into scanner_score for Apex tickers.
        // TODO(P3+): Feed RotationScanner with sector-level snapshots during ModeB.

        // P1-2.19: Record data age for telemetry.
        {
            let data_age_ns = if tick.recv_timestamp_ns > 0 && self.now_ns > tick.recv_timestamp_ns {
                self.now_ns - tick.recv_timestamp_ns
            } else if tick.timestamp_ns > 0 && self.now_ns > tick.timestamp_ns {
                self.now_ns - tick.timestamp_ns
            } else {
                0
            };
            if data_age_ns > 0 {
                self.telemetry.data_age_ring.record(data_age_ns);
            }
        }

        // P0-1.7: Stale tick filter — skip ENTRY signal generation for old ticks.
        // Still updates bar_history + exit tracking (already processed above).
        // STALE_TICK_MS threshold (not 200ms like HFT) — IBKR delayed data can be inherently stale.
        {
            let tick_age_ms = if tick.recv_timestamp_ns > 0 && self.now_ns > tick.recv_timestamp_ns {
                (self.now_ns - tick.recv_timestamp_ns) / 1_000_000
            } else if tick.timestamp_ns > 0 && self.now_ns > tick.timestamp_ns {
                (self.now_ns - tick.timestamp_ns) / 1_000_000
            } else {
                0 // No timestamp info = assume fresh
            };
            if tick_age_ms > self.config.hardening.ticks.stale_tick_ms {
                // Don't log every stale tick — just count in telemetry
                self.telemetry.ticks_stale.inc();
                return;
            }
        }

        // Skip signal generation if position exists or in cooldown
        if self.positions.contains_key(&tid) {
            return;
        }
        if let Some(&cooldown) = self.gap_cooldowns.get(&tid)
            && self.now_ns < cooldown
        {
            return;
        }

        // AUDIT-FIX: Exchange-open check. Don't trade tickers on closed exchanges.
        // HKEX closes 08:00 UTC, LSE 16:30 UTC, etc. Trading stale data from
        // closed exchanges produces immediate stop-outs (0-minute hold trades).
        {
            let exchange = self.broker.exchange_for_ticker(&tid);
            let utc_now = chrono::Utc::now();
            let exchange_open = match exchange {
                "HKEX" | "SEHK" => {
                    // HKEX: 01:30-08:00 UTC (09:30-16:00 HKT), no DST
                    let h = utc_now.hour();
                    let m = utc_now.minute();
                    (h > 1 || (h == 1 && m >= 30)) && h < 8
                }
                "TSEJ" | "TSE" => {
                    // TSE: 00:00-06:00 UTC (09:00-15:00 JST), no DST, lunch 02:30-03:30 UTC
                    let h = utc_now.hour();
                    h < 6
                }
                "ASX" => {
                    // ASX: 00:00-06:00 UTC (10:00-16:00 AEST), varies with DST
                    let h = utc_now.hour();
                    h < 6
                }
                "LSEETF" | "LSE" => {
                    // LSE: Use existing clock module (handles BST)
                    Clock::is_lse_open(time_secs)
                }
                "IBIS" | "XETRA" => {
                    // XETRA: 08:00-16:30 CET = 07:00-15:30 UTC (winter), 06:00-14:30 UTC (summer)
                    let h = utc_now.hour();
                    h >= 7 && h < 16
                }
                "SBF" | "EURONEXT" | "EURONEXT_PA" | "AEB" | "EURONEXT_AS" => {
                    let h = utc_now.hour();
                    h >= 8 && h < 17
                }
                "SMART" | "NYSE" | "NASDAQ" | "AMEX" => {
                    // US: 14:30-21:00 UTC (09:30-16:00 ET), varies with DST
                    let h = utc_now.hour();
                    h >= 14 && h < 21
                }
                "SGX" => {
                    let h = utc_now.hour();
                    h >= 1 && h < 9
                }
                "KSE" | "KRX" => {
                    let h = utc_now.hour();
                    h < 6
                }
                _ => true, // Unknown exchange: allow (fail-open)
            };
            if !exchange_open {
                return; // Exchange closed — skip signal entirely
            }
        }

        // Phase 11: Only ModeB allows new entries (replaces H35 cutoff + auction checks)
        if !self.current_mode.allows_entries() {
            return;
        }

        // Auction period check: no entries during auctions (ModeB refinement)
        if Clock::is_auction(time_secs) {
            return;
        }

        // P1-2.15: Economic calendar veto — no entries within 15 min of FOMC/CPI/NFP/BOE.
        {
            let utc_date = chrono::Utc::now().format("%Y-%m-%d").to_string();
            for ev in &self.economic_calendar {
                if ev.date == utc_date {
                    let window_secs = ev.window_mins * 60;
                    let event_start = ev.time_secs.saturating_sub(window_secs);
                    let event_end = ev.time_secs + window_secs;
                    if time_secs >= event_start && time_secs <= event_end {
                        eprintln!(
                            "ECON_CALENDAR: Blocking entry — event {} at {}s (window {}min)",
                            ev.name, ev.time_secs, ev.window_mins,
                        );
                        return;
                    }
                }
            }
        }

        // P12: Check if ticker is locked by predictive scorer (5 consecutive losses).
        if self.predictive_scorer.is_locked(tid) {
            return;
        }

        // Phase 1C: Block entry if jump-diffusion signature detected.
        if regime_decision.has_jump {
            eprintln!(
                "REGIME_GATE: ticker={} jump-diffusion detected, blocking entry",
                tid.0
            );
            return;
        }

        // P11: Check sector concentration before entry (33% cap per sector).
        let sector = sector_for_ticker(tid);
        if self.sector_tracker.is_over_concentrated(sector, self.portfolio.equity, self.arbiter.config.sector_heat_cap_pct) {
            return;
        }

        // P16: Liquidation defense — block entries if ISA allowance < 3% of equity.
        if self.liquidation_defense.should_block_entries(self.portfolio.equity) {
            return;
        }

        // P16: Liquidation defense — flatten if daily drawdown > 2%.
        self.liquidation_defense.update_drawdown(self.portfolio.equity);
        if self.liquidation_defense.should_flatten() && self.arbiter.regime < RiskRegime::Flatten {
            self.arbiter.regime = RiskRegime::Flatten;
        }

        // P16: Liquidation defense — halt if 3 consecutive stop losses (Blood Oath H12).
        if self.liquidation_defense.should_halt() {
            self.arbiter.regime = RiskRegime::Halt;
            self.halt_from_watchdog = false; // Not watchdog-caused — block auto-recovery
        }

        // P6-E: European session entry gating (supplement to ModeB check).
        // Only enforce XLON open hours during ModeB (European-only).
        // During ModeBPlus/ModeC, US tickers trade — don't gate on XLON.
        // During ModeA, Asian tickers trade — don't gate on XLON.
        if matches!(self.current_mode, TradingMode::ModeB) {
            if !self.european_session.entry_allowed("XLON", time_secs) {
                return;
            }
        }

        // Determine signal: from Python Brain (required for live/paper)
        // FIX 2026-03-11: Removed phantom fallback that generated 78% confidence
        // Long trades when Python bridge was dead. No signal = no trade.
        let Some(ref sig) = signal else {
            return; // No Python signal → no trade. Never generate phantom entries.
        };
        // Signal arrival instrumentation
        eprintln!(
            "SIGNAL_ARRIVED: ticker={} dir={} conf={:.1} kelly={:.4} shares={}",
            tid.0, sig.direction, sig.confidence, sig.kelly_fraction, sig.shares,
        );
        let direction = if sig.direction == "Short" {
            Direction::Short
        } else {
            Direction::Long
        };
        let confidence = sig.confidence;
        let kelly_fraction = sig.kelly_fraction;
        let strategy_name = sig.strategy.clone();
        let entry_type = sig.entry_type.clone();
        let shares_hint = sig.shares;

        // Risk arbiter evaluation with real time and spread
        let ticker_score = self.predictive_scorer.score(tid);
        // P0-1.10: Compute REAL tick age from receive timestamp (not hardcoded sentinel)
        let tick_age_secs = if tick.recv_timestamp_ns > 0 && self.now_ns > tick.recv_timestamp_ns {
            (self.now_ns - tick.recv_timestamp_ns) / 1_000_000_000
        } else if tick.timestamp_ns > 0 && self.now_ns > tick.timestamp_ns {
            // Fallback: use tick's own timestamp if recv_timestamp not available
            (self.now_ns - tick.timestamp_ns) / 1_000_000_000
        } else {
            0 // Fresh tick (no timestamp info available = assume live)
        };
        // P0-1.6 + P0-1.10: Get leverage factor from contract config for GARCH scaling
        let leverage_factor = self.config.contracts.get(tid.0 as usize)
            .map(|c| c.leverage as u32)
            .unwrap_or(1);
        let ctx = EvalContext {
            time_secs,
            last_tick_age_secs: tick_age_secs,
            bid: tick.bid,
            ask: tick.ask,
            broker_connected: self.broker.is_connected(),
            wal_available: self.wal.is_some(),
            now_ns: self.now_ns,
            kelly_fraction_raw: kelly_fraction,
            garch_sigma,
            leverage_factor,
            ticker_halted: false, // TODO(2.17): wire post-halt detection when circuit breaker tracking added
            ticker_ic: ticker_score.map_or(0.0, |s| s.ic),
            ticker_trade_count: ticker_score.map_or(0, |s| s.trade_count),
            ticker_locked: ticker_score.map_or(false, |s| s.locked),
            ticker_position_count: self.portfolio.position_count_for(&tid),
            // P2-3.4: Wire scanner_score from signal confidence (was -1.0 sentinel, silently passing CHECK 26).
            scanner_score: sig.confidence,
            // WIRED Sprint 2 (2026-03-21): pass computed values to risk evaluation
            evt_cvar,
            kalman_divergence,
            native_spread_bps,
            structural_score: sig.structural_score,
            ..EvalContext::default()
        };
        // Ouroboros ticker blacklist check (before risk arbiter — fast path rejection)
        if !self.arbiter.ticker_blacklist.is_empty() {
            if let Some(symbol) = self.broker.symbol_for(tid) {
                if self.arbiter.ticker_blacklist.iter().any(|b| b == &symbol) {
                    eprintln!("BLACKLIST: {} rejected by Ouroboros blacklist (WR < 30%%)", symbol);
                    self.telemetry.record_veto("TickerBlacklisted");
                    return;
                }
            }
        }

        let decision = self.arbiter.evaluate(
            tid,
            direction,
            confidence,
            kelly_fraction,
            &self.portfolio,
            &ctx,
        );
        if !decision.approved {
            // P4-A: Record veto in telemetry + log reason (no more silent vetoes)
            let reason_str = format!("{:?}", decision.reason);
            self.telemetry.record_veto(&reason_str);
            // Log first 10 vetoes per reason, then every 100th to avoid log flood
            let veto_count = self.telemetry.veto_count(&reason_str);
            if veto_count <= 10 || veto_count % 100 == 0 {
                let sym = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
                eprintln!(
                    "VETO: {} ticker={} sym={} reason={} (count={})",
                    if self.simulation_mode { "[SIM]" } else { "[LIVE]" },
                    tid.0, sym, reason_str, veto_count,
                );
            }
            // N2a: Write SignalRejected WAL event for missed-winner analysis.
            // Only write for meaningful rejections (not warmup/cooldown/closed).
            // Rate-limit: max 1 per ticker per 5 minutes to avoid WAL bloat.
            {
                let sym = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
                let spread_pct = if tick.bid > 0.0 {
                    (tick.ask - tick.bid) / tick.bid * 100.0
                } else {
                    0.0
                };
                self.write_wal(WalPayload::SignalRejected {
                    ticker_id: tid.0,
                    symbol: sym,
                    strategy: strategy_name.clone(),
                    confidence,
                    gate_name: "RiskArbiter".to_string(),
                    gate_reason: reason_str,
                    hurst: sig.hurst,
                    adx: sig.adx,
                    rvol: sig.rvol,
                    vol_slope: 0.0,
                    spread_pct,
                    price_at_reject: tick.last,
                });
            }
            return;
        }
        // P4-A: Record signal generation and approval in telemetry
        self.telemetry.signals_generated.inc();
        self.telemetry.signals_approved.inc();

        // L1 data quality gate: only enter positions on tickers with true tick-by-tick data.
        // MktData (250ms snapshot → 5s synthetic bar) is too coarse for signal validation.
        // BYPASS in simulation_mode: paper accounts support only ~7 concurrent L1 streams
        // (IBKR error 10190), but MktData is the primary data source for paper trading.
        // This gate only constrains LIVE mode where we need continuous tape for execution.
        if !self.simulation_mode && !self.broker.is_l1_subscribed(&tid) {
            eprintln!(
                "L1_GATE: ticker={} signal approved but no L1 data — skipping entry",
                tid.0,
            );
            return;
        }

        // P6-C: ISA gate check before order submission.
        // tick.ask is already GBP-converted, so trade_value is in GBP.
        // Simulation: Kelly-anchored sizing with £500 base. Live: Python shares.
        // Half-Kelly during early validation (< 250 trades).
        // Applied BEFORE sizing so positions are actually half-sized, not just logged as half.
        let total_trades_for_ramp = self.simulated_trades.len() as u64 + self.telemetry.orders_filled.get();
        let sizing_kelly = if total_trades_for_ramp < 250 {
            kelly_fraction * 0.5
        } else {
            kelly_fraction
        };
        let trade_value_gbp = if self.simulation_mode {
            // AUDIT-FIX (2026-03-18): Kelly × equity — institutional sizing.
            // Half-Kelly applied above for <250 trades (bootstrap phase).
            // FIXED (Sprint 5, SK-01): Use equity_for_sizing (entry-based) not marked equity.
            // Prevents undersizing after unrealised losses.
            let notional = sizing_kelly * self.portfolio.equity_for_sizing;
            notional.clamp(100.0, self.portfolio.equity_for_sizing * 0.25) // Floor £100, cap 25%
        } else {
            tick.ask * shares_hint.max(1) as f64
        };
        // P3-D: SmartRouter — check ETP route (passthrough in Crucible mode).
        let symbol = self.config.contracts.get(tid.0 as usize)
            .map(|c| c.symbol.as_str())
            .unwrap_or("UNKNOWN");
        if let Some(_etp) = self.smart_router.find_etp(symbol) {
            // ETP mapping exists — route through ETP wrapper (already using ETP in Crucible)
        }
        // P6-B: Exchange profile — validate tick rounding via exchange registry.
        let ticker_exchange = self.broker.exchange_for_ticker(&tid);
        // P0-1.6: Debug log exchange MIC resolution for ISA gate diagnostics.
        eprintln!("ISA_GATE_DEBUG: ticker={} broker_exchange={} → resolving MIC", tid.0, ticker_exchange);
        let exchange_mic = match ticker_exchange {
            "LSEETF" | "LSE" => "XLON",
            "TSEJ" | "TSE" => "XTKS",
            "SEHK" | "HKEX" => "XHKG",
            "KSE" | "KRX" => "XKRX",
            "ASX" => "XASX",
            "SGX" => "XSES",
            "IBIS" | "XETRA" => "XETR",
            "SBF" | "EURONEXT" | "EURONEXT_PA" => "XPAR",
            "AEB" | "EURONEXT_AS" => "XAMS",
            "SMART" | "NYSE" | "NASDAQ" | "AMEX" => "XNYS",
            _ => "XLON",
        };
        if let Some(profile) = self.exchange_registry.by_mic(exchange_mic) {
            let rounded = profile.round_tick(tick.ask);
            if (rounded - tick.ask).abs() > f64::EPSILON {
                eprintln!(
                    "TICK_ROUND: ticker={} ask={:.4} rounded={:.4} (tick size mismatch)",
                    tid.0, tick.ask, rounded,
                );
            }
        }
        let isa_check = self.isa_gate.check(exchange_mic, trade_value_gbp);
        if !matches!(isa_check, crate::isa_gate::IsaCheckResult::Allowed) {
            eprintln!("ISA_GATE: rejected trade for ticker={}, result={isa_check:?}", tid.0);
            return;
        }

        // Minimum position size gate: reject dust trades.
        // Simulation: £20 min (need data). Live: £1500 min (commission drag).
        let min_trade_gbp = if self.simulation_mode { 20.0 } else { 1500.0 };
        if trade_value_gbp < min_trade_gbp {
            return;
        }

        // Use sizing_kelly (half-Kelly already applied above for <250 trades)
        let kelly_fraction = sizing_kelly;

        // Submit order — use engine's monotonic counter for unique IDs
        self.order_counter += 1;
        let order_id = format!("order-{}", self.order_counter);
        // P21-FX: Use NATIVE currency ask price for IBKR limit order (IBKR expects native).
        let limit_price =
            native_ask * (1.0 + self.config.execution.marketable_limit_buffer_pct / 100.0);
        // Round to tick size (H65)
        let limit_price = round_to_tick_size(limit_price, &self.config);

        // Position size: In simulation mode, use Kelly-anchored notional (£500 base).
        // In live mode, use Kelly-computed shares from Python signal.
        // HIGH-PRICE GUARD: If 1 share costs more than the Kelly notional cap (£2000),
        // skip entry entirely. Forcing min 1 share on £10k+ tickers creates unrealistic
        // concentration that poisons simulation data quality.
        if self.simulation_mode && tick.ask > self.config.hardening.sizing.high_price_guard_sim {
            eprintln!(
                "SIZING_SKIP: ticker={} price=£{:.0} exceeds £{:.0} sim cap — no fractional shares",
                tid.0, tick.ask, self.config.hardening.sizing.high_price_guard_sim,
            );
            return;
        }
        let qty = if self.simulation_mode {
            // Use the Kelly-anchored trade_value_gbp computed above.
            (trade_value_gbp / tick.ask).max(1.0) as u32
        } else if shares_hint > 0 {
            shares_hint
        } else {
            (decision.adjusted_size / native_ask).max(1.0) as u32
        };

        // P1-2.11: Board lot rounding — TSE/HKEX/SGX require 100-share lots.
        // Without this, simulation produces untradeable fractional lot sizes,
        // breaking sim/live coherence for Asian exchanges.
        let exchange_for_lot = self.config.contracts.get(tid.0 as usize)
            .map(|c| c.exchange.as_str())
            .unwrap_or("XLON");
        let min_lot = min_lot_for_exchange(exchange_for_lot);
        let qty = if min_lot > 1 {
            let rounded = ((qty as f64 / min_lot as f64).round() as u32) * min_lot;
            if rounded == 0 {
                eprintln!(
                    "LOT_SKIP: ticker={} qty={} < min_lot={} on {} — skipping entry",
                    tid.0, qty, min_lot, exchange_for_lot,
                );
                return;
            }
            rounded
        } else {
            qty
        };

        // Get symbol name for logging
        let symbol_name = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
        let currency = self.ticker_currencies.get(&tid).cloned().unwrap_or_else(|| "GBP".to_string());

        self.write_wal(WalPayload::RoutedOrder {
            order_id: order_id.clone(),
            ticker_id: tid.0,
            side: format!("{direction:?}"),
            confidence,
            strategy: strategy_name.clone(),
            kelly_fraction,
            approved_size: decision.adjusted_size,
            symbol: symbol_name.clone(),
            qty,
            currency: currency.clone(),
            entry_rvol: sig.rvol,
            entry_hurst: sig.hurst,
            entry_adx: sig.adx,
            rsi: sig.rsi,
            vwap_dist_pct: sig.vwap_dist_pct,
            atr: 0.0,
            vol_slope: sig.vol_slope,
            spread_pct: if tick.ask > 0.0 && tick.bid > 0.0 { ((tick.ask - tick.bid) / tick.ask) * 100.0 } else { 0.0 },
            mtf_score: sig.structural_score,
            entry_type: entry_type.clone(),
            ibs: sig.ibs,
        });
        self.tracked_orders.push(order_id.clone());

        if self.simulation_mode {
            // SIMULATION MODE: Do NOT submit to broker. Create simulated fill immediately.
            let sim_trade = SimulatedTrade {
                order_id: order_id.clone(),
                ticker_id: tid,
                symbol: symbol_name.clone(),
                direction,
                qty,
                fill_price_native: native_ask,
                fill_price_gbp: tick.ask, // tick.ask is already GBP-converted
                currency: currency.clone(),
                confidence,
                kelly_fraction,
                trade_value_gbp: tick.ask * qty as f64,
                timestamp_ns: self.now_ns,
                strategy: strategy_name.clone(),
                status: SimTradeStatus::Open,
                exit_price_gbp: None,
                pnl_gbp: None,
                exit_timestamp_ns: None,
                entry_rvol: sig.rvol,
                entry_hurst: sig.hurst,
                entry_adx: sig.adx,
                entry_vol_slope: sig.vol_slope,
                entry_vwap_dist_pct: sig.vwap_dist_pct,
                entry_structural_score: sig.structural_score,
            };

            // Enriched SIM_TRADE log: full explainability per trade
            let regime_str = format!("{:?}", self.arbiter.regime);
            let mode_str = format!("{:?}", self.current_mode);
            let evidence_maturity = if total_trades_for_ramp < 50 { "bootstrap" }
                else if total_trades_for_ramp < 250 { "low-confidence" }
                else { "mature" };
            // Live-style constrained view: what would a £15k account do?
            let live_equity = 15000.0_f64;
            let live_kelly_notional = live_equity * kelly_fraction * 0.25; // quarter-Kelly for live
            let live_would_qualify = live_kelly_notional >= 100.0;
            eprintln!(
                "SIM_TRADE: {} {:?} {} x{} @ {:.4} {} (GBP {:.4}) val=£{:.2} conf={:.0} kelly={:.4} regime={} mode={} evidence={} strategy={} exchange={} | LIVE_VIEW: notional=£{:.0} qualify={}",
                order_id, direction, symbol_name, qty,
                native_ask, currency, tick.ask,
                sim_trade.trade_value_gbp, confidence, kelly_fraction,
                regime_str, mode_str, evidence_maturity, strategy_name,
                exchange_mic, live_kelly_notional, live_would_qualify,
            );

            // Track as position for exit management (Chandelier ladder)
            let entry_price_gbp = tick.ask;
            // AUDIT-FIX: Use 1.5×ATR for initial stop (matches Chandelier Rung 1).
            // Fallback to 5% if ATR unavailable (cold start).
            let atr_val = self.bar_history.get(&tid).map(|h| h.atr(14)).unwrap_or(0.0);
            let stop_pct = if atr_val > 0.0 && entry_price_gbp > 0.0 {
                // P2-#11/#13: Read ATR mult and clamp from config (was hardcoded 1.5, 0.01-0.10)
                (self.config.chandelier.initial_stop_atr_mult * atr_val / entry_price_gbp)
                    .clamp(self.config.hardening.sizing.stop_pct_clamp_min, self.config.hardening.sizing.stop_pct_clamp_max)
            } else {
                self.config.hardening.sizing.cold_start_stop_pct // P2-#12: Was hardcoded 0.05
            };
            let stop = initial_stop_price(entry_price_gbp, stop_pct);
            self.positions.insert(tid, PositionState {
                ticker_id: tid,
                qty,
                avg_entry: entry_price_gbp,
                stop_price: stop,
                entry_timestamp_ns: self.now_ns,
                origin_order_id: order_id.clone(),
                unrealized_pnl: 0.0,
                realized_pnl: 0.0,
                highest_high: entry_price_gbp,
                total_commission: 0.0,
                trailing_rung: 0,
                state: OrderState::Filled,
                is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: entry_type.clone(),
                active_trading_ticks: 0,
            });

            self.simulated_trades.push(sim_trade);

            // P0-1.4: Increment Kelly ramp counter on each fill (paper + live).
            // This is THE validation counter — positions scale from 10% to 100% Kelly over 250 fills.
            self.arbiter.config.kelly_ramp_trades += 1;
            self.write_wal(WalPayload::KellyRampAdvance {
                count: self.arbiter.config.kelly_ramp_trades as u64,
            });
            if self.arbiter.config.kelly_ramp_trades % 25 == 0 || self.arbiter.config.kelly_ramp_trades <= 5 {
                let ramp_pct = ((self.arbiter.config.kelly_ramp_trades as f64 / self.arbiter.config.kelly_ramp_target as f64).clamp(0.1, 1.0) * 100.0) as u32;
                eprintln!(
                    "KELLY_RAMP: trade #{} → sizing at {}% Kelly",
                    self.arbiter.config.kelly_ramp_trades, ramp_pct,
                );
            }

            // N0a: Increment daily trade count for frequency management.
            self.portfolio.daily_trade_count += 1;

            // Also add to portfolio for HEARTBEAT tracking and risk calculations
            let pos_copy = PositionState {
                ticker_id: tid,
                qty,
                avg_entry: entry_price_gbp,
                stop_price: stop,
                entry_timestamp_ns: self.now_ns,
                origin_order_id: order_id.clone(),
                unrealized_pnl: 0.0,
                realized_pnl: 0.0,
                highest_high: entry_price_gbp,
                total_commission: 0.0,
                trailing_rung: 0,
                state: OrderState::Filled,
                is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: entry_type.clone(),
                active_trading_ticks: 0,
            };
            self.portfolio.add_position(pos_copy);

            self.telemetry.orders_submitted.inc();
            self.telemetry.orders_filled.inc();

        } else {
            // LIVE MODE: Submit to broker
            if let Err(e) = self
                .broker
                .submit_order(&order_id, tid, OrderSide::Buy, qty, limit_price)
            {
                eprintln!(
                    "ORDER_REJECTED: ticker={} order_id={} qty={} limit={:.4} error={:?}",
                    tid.0, order_id, qty, limit_price, e
                );
                self.telemetry.orders_rejected.inc();
                // P1-2.10: Fire-and-forget Telegram alert for ORDER_REJECTED.
                let msg = format!(
                    "⚠️ ORDER_REJECTED: ticker={} qty={} limit={:.4} err={:?}",
                    tid.0, qty, limit_price, e
                );
                let _ = std::process::Command::new("python3")
                    .args(["-c", &format!(
                        "from python_brain.ouroboros.telegram_notify import send_alert; send_alert('{}')",
                        msg.replace('\'', "\\'")
                    )])
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .spawn();
                return;
            }
            self.telemetry.orders_submitted.inc();
        }

        // P3-C: Track order in Executioner for lifecycle management
        self.executioner.track_order(TrackedOrder {
            order_id: order_id.clone(),
            ticker_id: tid,
            lifecycle: OrderLifecycle::Submitted,
            submit_ns: self.now_ns,
            last_update_ns: self.now_ns,
            qty,
            filled_qty: if self.simulation_mode { qty } else { 0 },
            limit_price,
            retries: 0,
            is_exit: false,
        });

        // T2T latency logging (H118) + P22: Latency profiler.
        let t2t_ns = self.now_ns.saturating_sub(t2t_start_ns);
        if t2t_ns > 0 {
            let t2t_ms = t2t_ns as f64 / 1_000_000.0;
            self.latency_profiler.record_ms(PipelineStage::TickToTrade, t2t_ms);
            eprintln!(
                "T2T: ticker={} {} latency={:.3}ms qty={qty} price={limit_price:.4} {}",
                tid.0, symbol_name, t2t_ms,
                if self.simulation_mode { "[SIMULATED]" } else { "[LIVE]" },
            );
        }

        // Drain broker events
        let events = self.broker.drain_events();
        for ev in &events {
            self.process_broker_event(ev);
        }
    }

    /// Run position reconciliation (every 5 min during trading).
    /// P3-C: Check for stale orders and prune completed ones.
    pub fn check_executioner(&mut self) {
        // Detect stale unacked orders
        let stale_unacked: Vec<String> = self
            .executioner
            .stale_unacked(self.now_ns)
            .iter()
            .map(|o| o.order_id.clone())
            .collect();
        for oid in &stale_unacked {
            eprintln!("EXECUTIONER: stale unacked order {oid}, cancelling");
            let _ = self.broker.cancel_order(oid);
            self.executioner.update_lifecycle(oid, OrderLifecycle::Cancelled, self.now_ns);
        }

        // Detect stale unfilled orders
        let stale_unfilled: Vec<String> = self
            .executioner
            .stale_unfilled(self.now_ns)
            .iter()
            .map(|o| o.order_id.clone())
            .collect();
        for oid in &stale_unfilled {
            eprintln!("EXECUTIONER: stale unfilled order {oid}, cancelling");
            let _ = self.broker.cancel_order(oid);
            self.executioner.update_lifecycle(oid, OrderLifecycle::Cancelled, self.now_ns);
        }

        // Prune completed orders
        self.executioner.prune_completed();
    }

    /// P2-A: Writes ReconciliationDivergence to WAL on any mismatch.
    /// P4-B: Runs hardening invariant checks after reconciliation.
    pub fn reconcile(&mut self) -> Result<reconciler::ReconcileResult, EngineError> {
        let positions = self.broker.request_positions()?;
        let result = reconciler::reconcile_positions(&self.portfolio, &positions);
        if !result.is_clean {
            let mismatch_strs: Vec<String> = result
                .mismatches
                .iter()
                .map(|m| format!("{m:?}"))
                .collect();
            eprintln!(
                "CRITICAL: Reconciliation mismatch: {} issues",
                result.mismatches.len()
            );
            self.write_wal(WalPayload::ReconciliationDivergence {
                mismatches: mismatch_strs,
                timestamp_ns: self.now_ns,
            });
            self.arbiter.regime = RiskRegime::Flatten;
        }

        // FIX Issue #8: Mark-to-market — update equity from current prices
        self.portfolio.mark_to_market(&self.last_prices);
        self.portfolio.update_high_water();

        // P4-A: Record reconciliation in telemetry
        self.telemetry.reconciliation_runs.inc();
        if !result.is_clean {
            self.telemetry.reconciliation_mismatches.inc();
        }

        // P4-B: Hardening invariant checks
        self.verify_hardening_invariants();

        // P6-A: Check FX rate staleness
        if self.fx_table.is_stale(self.now_ns) {
            eprintln!("WARNING: FX rates stale (>24h) — using defaults");
        }

        // FIX 3: Reduce → Normal auto-recovery.
        // If regime is Reduce and ALL spoof detectors are normalized (last 5 spreads
        // within 3× median), no liquidation defense active, and no macro escalation,
        // de-escalate to Normal. This prevents permanent Reduce from startup false positives.
        if self.arbiter.regime == RiskRegime::Reduce
            && !self.liquidation_defense.should_flatten()
            && !self.liquidation_defense.should_halt()
            && !self.cross_timezone.should_reduce_exposure()
            && !self.macro_regime.should_escalate_regime()
            && !self.broker_health.should_reduce(self.now_ns)
            && self.portfolio.consecutive_stop_losses < 3
        {
            // Check that no tickers are currently spoofed
            let any_spoofed = self.universe.tickers.keys().any(|&tid| {
                self.quote_imbalance.is_spoofed(tid)
            });
            if !any_spoofed {
                eprintln!("REGIME_RECOVERY: Reduce → Normal (all triggers cleared)");
                self.arbiter.regime = RiskRegime::Normal;
                self.write_wal(WalPayload::RiskStateChange {
                    from: "Reduce".to_string(),
                    to: "Normal".to_string(),
                    trigger: "auto_clear_all_triggers_resolved".to_string(),
                });
            }
        }

        // P21-AUTO: Halt → Normal auto-recovery.
        // FIXED (Sprint 5, SK-02): Added time-based recovery to prevent zombie halt.
        // If broker is connected and halt has been active >30min, auto-recover to Reduce
        // (not Normal — still cautious after extended halt).
        // Track when halt started (using halt_started_ns field on engine).
        // Set when entering HALT, reset when leaving.
        let halt_duration_ns = if self.arbiter.regime == RiskRegime::Halt {
            if self.halt_started_ns == 0 {
                self.halt_started_ns = self.now_ns; // First time seeing HALT
            }
            self.now_ns.saturating_sub(self.halt_started_ns)
        } else {
            self.halt_started_ns = 0; // Not in HALT, reset
            0
        };
        let halt_timeout_ns: u64 = 30 * 60 * 1_000_000_000; // 30 minutes

        if self.arbiter.regime == RiskRegime::Halt
            && !self.halt_from_watchdog
            && !self.liquidation_defense.should_halt()
            && !self.panic_guard.has_panicked()
            && !self.broker_health.should_halt(self.now_ns)
            && self.portfolio.consecutive_stop_losses < self.config.risk.consecutive_loss_halt
        {
            eprintln!("REGIME_RECOVERY: Halt → Normal (all halt sources cleared, broker healthy)");
            self.arbiter.regime = RiskRegime::Normal;
            self.write_wal(WalPayload::RiskStateChange {
                from: "Halt".to_string(),
                to: "Normal".to_string(),
                trigger: "auto_clear_all_halt_sources_resolved".to_string(),
            });
        }
        // SK-02 TIME-BASED FALLBACK: If halt has been active >30min and broker is connected,
        // recover to REDUCE (not Normal — still cautious). Prevents zombie halt.
        else if self.arbiter.regime == RiskRegime::Halt
            && halt_duration_ns > halt_timeout_ns
            && self.broker.is_connected()
        {
            eprintln!(
                "REGIME_RECOVERY: Halt → Reduce (zombie halt timeout after {}min, broker connected)",
                halt_duration_ns / 60_000_000_000
            );
            self.arbiter.regime = RiskRegime::Reduce;
            self.write_wal(WalPayload::RiskStateChange {
                from: "Halt".to_string(),
                to: "Reduce".to_string(),
                trigger: "zombie_halt_timeout_30min".to_string(),
            });
        }

        // P6-F: Cross-timezone carry risk assessment
        let carry_count = self.carry_manager.len();
        let carry_pnl = self.carry_manager.total_carry_pnl();
        let risk_score = self.cross_timezone.carry_risk(carry_count, carry_pnl, self.now_ns);
        if self.cross_timezone.should_reduce_exposure()
            && self.arbiter.regime < RiskRegime::Reduce
        {
            eprintln!(
                "CROSS_TZ: carry risk {risk_score:.2} → escalating to REDUCE"
            );
            self.arbiter.regime = RiskRegime::Reduce;
            self.telemetry.regime_escalations.inc();
        }

        // P9: Cross-asset macro regime check.
        let macro_signal = self.macro_regime.evaluate();
        if self.macro_regime.should_escalate_regime() && self.arbiter.regime < RiskRegime::Reduce {
            eprintln!("MACRO: {:?} → escalating to REDUCE", macro_signal);
            self.arbiter.regime = RiskRegime::Reduce;
        }

        // P17: Broker health monitoring.
        if self.broker_health.should_halt(self.now_ns) {
            eprintln!("BROKER_HEALTH: disconnect >120s → HALT");
            self.arbiter.regime = RiskRegime::Halt;
            self.halt_from_watchdog = false; // Not watchdog-caused — block auto-recovery
        }
        if self.broker_health.should_reduce(self.now_ns) && self.arbiter.regime < RiskRegime::Reduce {
            eprintln!(
                "BROKER_HEALTH: fill error rate {:.1}% → REDUCE",
                self.broker_health.fill_error_rate_1min(self.now_ns) * 100.0
            );
            self.arbiter.regime = RiskRegime::Reduce;
        }

        // P18: WAL compressor — check if rotation needed (1M events).
        if self.wal_compressor.needs_rotation() {
            let target = self.wal_compressor.rotation_target_path();
            eprintln!("WAL_COMPRESSOR: rotation needed → {target}");
            self.wal_compressor.reset_counter();
        }

        // P19: State checkpoint (hourly hash verification).
        if self.checkpoint_mgr.needs_checkpoint(self.now_ns) {
            let hash = self.checkpoint_mgr.last_hash().unwrap_or(0);
            eprintln!("CHECKPOINT: hash={hash:#018x}");
        }

        // Simulation mode: prevent regime escalation (no stale VIX/macro data).
        if self.simulation_mode && self.arbiter.regime > RiskRegime::Normal {
            self.arbiter.regime = RiskRegime::Normal;
        }

        // P6-HEARTBEAT: 5-minute status heartbeat for operational monitoring.
        let five_min_ns = 300_000_000_000u64;
        if self.now_ns >= self.last_heartbeat_ns + five_min_ns {
            let positions_count = self.positions.len();
            let equity = self.portfolio.equity;
            let daily_pnl = self.portfolio.daily_pnl;
            let regime = format!("{:?}", self.arbiter.regime);
            let session_mode = format!("{}", self.session_mgr.mode());
            let clock_mode = format!("{:?}", self.current_mode);
            let session = format!("{}", self.current_trading_session);
            let ticks = self.telemetry.ticks_received.get();
            let signals = self.telemetry.signals_generated.get();
            let entries = self.telemetry.orders_submitted.get();
            eprintln!(
                "HEARTBEAT: regime={regime} session_mode={session_mode} clock={clock_mode} \
                 session={session} equity={equity:.2} pnl={daily_pnl:.2} pos={positions_count} \
                 ticks={ticks} signals={signals} orders={entries}"
            );

            // Write per-position snapshot to WAL for Google Sheets unrealised P&L
            if !self.positions.is_empty() {
                let mut pos_list = Vec::new();
                let mut total_unrealized = 0.0f64;
                for (&tid, pos) in &self.positions {
                    let symbol = self.broker.symbol_for(tid)
                        .unwrap_or_else(|| format!("T{}", tid.0));
                    let current_price = self.last_prices.get(&tid).copied()
                        .unwrap_or(pos.avg_entry);
                    let unrealized = (current_price - pos.avg_entry) * pos.qty as f64;
                    total_unrealized += unrealized;
                    pos_list.push(serde_json::json!({
                        "ticker_id": tid.0,
                        "symbol": symbol,
                        "qty": pos.qty,
                        "entry_price": (pos.avg_entry * 10000.0).round() / 10000.0,
                        "current_price": (current_price * 10000.0).round() / 10000.0,
                        "unrealized_pnl": (unrealized * 100.0).round() / 100.0,
                        "rung": pos.trailing_rung,
                        "stop_price": (pos.stop_price * 10000.0).round() / 10000.0,
                        "highest_high": (pos.highest_high * 10000.0).round() / 10000.0,
                    }));
                }
                self.write_wal(WalPayload::StateSnapshot {
                    portfolio_json: format!(
                        "{{\"positions\":{},\"high_water\":{:.2},\"unrealized_pnl\":{:.2}}}",
                        self.positions.len(),
                        self.portfolio.high_water_mark,
                        total_unrealized
                    ),
                    equity: self.portfolio.equity,
                    high_water: self.portfolio.high_water_mark,
                    hash: String::new(),
                    open_positions: pos_list,
                });
            }

            self.last_heartbeat_ns = self.now_ns;
        }

        // P21: Session manager update.
        let london_secs = self.london_time_secs();
        if let Some(transition) = self.session_mgr.update(london_secs, !self.positions.is_empty(), self.now_ns) {
            eprintln!("SESSION: {:?} → {:?}", transition.from, transition.to);
        }

        // Phase 2: Market scheduler — update 6-phase global session from UTC clock.
        {
            let epoch_secs = self.now_ns / 1_000_000_000;
            let utc_dt = chrono::DateTime::<chrono::Utc>::from_timestamp(epoch_secs as i64, 0)
                .unwrap_or_else(|| chrono::Utc::now());
            let new_session = market_scheduler::get_current_session(utc_dt);
            if new_session != self.current_trading_session {
                eprintln!(
                    "MARKET_SCHEDULER: {} -> {}",
                    self.current_trading_session, new_session
                );
                self.current_trading_session = new_session;
            }
        }

        // P21: Subscription rotation on mode transitions
        let current_mode = self.session_mgr.mode();
        if current_mode != self.last_session_mode {
            eprintln!(
                "MODE_TRANSITION: {} → {} (will rotate subscriptions)",
                self.last_session_mode, current_mode
            );

            // Carry manager — freeze/unfreeze stops at session boundaries
            #[allow(deprecated)]
            match (self.last_session_mode, current_mode) {
                // Active → Dark/Carry: freeze stops (entering maintenance/overnight)
                (_, SessionMode::Dark) | (_, SessionMode::Carry)
                    if !matches!(self.last_session_mode, SessionMode::Dark | SessionMode::Carry) =>
                {
                    let frozen = self.carry_manager.freeze_all_stops(self.now_ns);
                    if frozen > 0 {
                        let tickers: Vec<TickerId> = self.positions.keys().copied().collect();
                        for tid in tickers {
                            if self.carry_manager.is_carried(&tid)
                                && let Some(pos) = self.positions.get_mut(&tid) {
                                pos.is_carried = true;
                            }
                        }
                    }
                }
                // Dark/Carry → Active: unfreeze stops
                (SessionMode::Dark, SessionMode::Active) |
                (SessionMode::Carry, SessionMode::Active) |
                (SessionMode::Dark, SessionMode::ModeA) |
                (SessionMode::Carry, SessionMode::ModeA) |
                (SessionMode::Carry, SessionMode::ModeB) |
                (SessionMode::Carry, SessionMode::ModeC) => {
                    let unfrozen = self.carry_manager.unfreeze_all_stops();
                    if unfrozen > 0 {
                        let tickers: Vec<TickerId> = self.positions.keys().copied().collect();
                        for tid in tickers {
                            if !self.carry_manager.is_carried(&tid)
                                && let Some(pos) = self.positions.get_mut(&tid) {
                                pos.is_carried = false;
                            }
                        }
                    }
                }
                _ => {} // Other transitions don't affect carry state
            }

            self.apply_mode_subscription_rotation(current_mode);
            self.last_session_mode = current_mode;
        }

        // P22: Latency profiler — check for bottleneck.
        if let Some(stage) = self.latency_profiler.bottleneck() {
            let stats = self.latency_profiler.stats(stage);
            if stats.p99_ms > 500.0 {
                eprintln!("LATENCY: bottleneck at {:?} — p99={:.1}ms", stage, stats.p99_ms);
            }
        }

        // P3-E: SubscriptionManager — prune stale deferrals.
        self.subscription_manager.prune_deferrals(self.now_ns);

        // P15: Check for pending split events.
        self.check_pending_splits();

        // P6-D: Log Asian session status during Mode A hours.
        let utc_secs = (self.now_ns / 1_000_000_000) as u32 % 86400;
        if self.asian_session.is_mode_a(utc_secs) {
            let open = self.asian_session.open_exchanges(utc_secs);
            if !open.is_empty() {
                eprintln!("ASIAN_SESSION: {} exchanges open: {:?}", open.len(), open);
            }
        }

        // P5-C: Log-Thompson sampler — compute allocation ranking.
        // WIRED (Sprint 2C): Top-K ranking written to file for Python consumption.
        let top_tickers = self.thompson_sampler.select_top_k(5);
        // Write top-K to shared file for ticker_selector to consume
        if let Ok(json) = serde_json::to_string(&top_tickers) {
            let _ = std::fs::write("/app/data/thompson_top_k.json", &json);
        }

        // WIRED (Sprint 3C): Write hottest sector for nightly briefing data.
        if let Some((sector, exposure)) = self.sector_tracker.hottest_sector() {
            let sector_json = format!(
                r#"{{"hottest_sector":"{:?}","exposure_gbp":{:.2},"timestamp_ns":{}}}"#,
                sector, exposure, self.now_ns
            );
            let _ = std::fs::write("/app/data/sector_hottest.json", &sector_json);
        }

        // Hot-reload watchlist if ticker_selector has updated it.
        self.maybe_reload_watchlist();

        // 80/20 Dynamic Ticker Scanning Architecture:
        // - Core 80 slots: refreshed on watchlist file change (ticker_selector runs every 15 min).
        //   Core tickers are session-aware top-ranked instruments that stay subscribed.
        // - Dark horse 20 slots: rotated every dark_horse_rotation_secs (default 15 min).
        //   Dark horses are unusual movers (RVOL spike, gap, volume outlier) from Python ranker.
        //
        // The full rotation (core + dark horse) happens on:
        //   1. Watchlist file change (maybe_reload_watchlist detects mtime change)
        //   2. Mode transitions (apply_mode_subscription_rotation)
        //
        // The dark-horse-only rotation happens on the dark_horse_rotation_secs timer.
        let dh_rotation_ns = self.config.scanner.dark_horse_rotation_secs as u64 * 1_000_000_000;
        // Full watchlist rotation on 15-min timer (keeps core stable, refreshes dark horses).
        const ROTATION_INTERVAL_NS: u64 = 15 * 60 * 1_000_000_000; // 15 minutes
        if self.now_ns >= self.last_watchlist_rotation_ns + ROTATION_INTERVAL_NS {
            let mode = self.session_mgr.mode();
            #[allow(deprecated)]
            if matches!(mode, SessionMode::Active
                | SessionMode::ModeA | SessionMode::ModeB
                | SessionMode::ModeBPlus | SessionMode::ModeC) {
                eprintln!("SCANNER_80_20: full rotation triggered (mode={})", mode);
                self.rotate_subscriptions_from_watchlist();
            }
        } else if dh_rotation_ns > 0
            && self.now_ns >= self.last_dark_horse_rotation_ns + dh_rotation_ns
        {
            // Dark-horse-only rotation: swap just the 20 dark horse slots.
            let mode = self.session_mgr.mode();
            #[allow(deprecated)]
            if matches!(mode, SessionMode::Active
                | SessionMode::ModeA | SessionMode::ModeB
                | SessionMode::ModeBPlus | SessionMode::ModeC) {
                eprintln!("SCANNER_80_20: dark horse rotation triggered (mode={})", mode);
                self.rotate_dark_horse_slots();
            }
        }

        self.last_reconcile_ns = self.now_ns;
        Ok(result)
    }

    /// Hot-reload watchlist file if the ticker selector has updated it.
    /// Checks the unified watchlist only.
    /// Only ADDS new tickers — never removes (positions may be open).
    fn maybe_reload_watchlist(&mut self) {
        let watchlist_files = [
            "config/active_watchlist.json",
        ];

        for wl_path_str in &watchlist_files {
            let path = std::path::Path::new(wl_path_str);
            let current_mtime = match std::fs::metadata(path)
                .and_then(|m| m.modified())
                .map(|t| t.duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_secs())
            {
                Ok(t) => t,
                Err(_) => continue, // File doesn't exist yet — skip
            };

            let last_mtime = self.watchlist_mtimes.get(*wl_path_str).copied().unwrap_or(0);
            if current_mtime <= last_mtime {
                continue; // No change
            }

            let tickers = match crate::config_loader::EngineConfig::load_universe_from_watchlist(path) {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("WATCHLIST_RELOAD: failed to parse {}: {}", wl_path_str, e);
                    continue;
                }
            };

            let before_count = self.universe.intern.len();
            for raw in &tickers {
                let tid = self.universe.register(&raw.symbol, UniverseClass::Vanguard);
                self.bar_history.entry(tid).or_insert_with(|| BarHistory::new(300));
                self.thompson_sampler.register(tid);
            }
            let added = self.universe.intern.len() - before_count;

            self.watchlist_mtimes.insert(wl_path_str.to_string(), current_mtime);
            if added > 0 {
                eprintln!(
                    "WATCHLIST_RELOAD: {} → added {} new tickers (total: {})",
                    wl_path_str, added, self.universe.intern.len()
                );
            }
        }
    }

    /// Load top N ticker symbols from a watchlist JSON file.
    /// Returns (symbols, exchange_hints, currency_hints) for dynamic contract registration.
    /// Falls back to empty vec if file doesn't exist or parse fails.
    fn load_watchlist_tickers(path: &str, max_count: usize) -> Vec<(String, String, String)> {
        let content = match std::fs::read_to_string(path) {
            Ok(c) => c,
            Err(_) => return Vec::new(),
        };
        let parsed: serde_json::Value = match serde_json::from_str(&content) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("WATCHLIST_LOAD: failed to parse {}: {}", path, e);
                return Vec::new();
            }
        };
        let vanguard = match parsed.get("vanguard").and_then(|v| v.as_array()) {
            Some(arr) => arr,
            None => return Vec::new(),
        };
        let mut result = Vec::new();
        for entry in vanguard.iter().take(max_count) {
            let symbol = entry.get("symbol").and_then(|s| s.as_str()).unwrap_or("").to_string();
            if symbol.is_empty() { continue; }
            let exchange = entry.get("exchange").and_then(|s| s.as_str()).unwrap_or("SMART").to_string();
            // P21-FX: Infer currency from symbol suffix when not specified in watchlist JSON.
            // .L = GBP (LSE), .DE/.AS/.PA/.BR/.LS/.MI = EUR, default = USD (US equities).
            let default_currency = if symbol.ends_with(".L") {
                "GBP"
            } else if symbol.ends_with(".DE") || symbol.ends_with(".AS") || symbol.ends_with(".PA")
                   || symbol.ends_with(".BR") || symbol.ends_with(".LS") || symbol.ends_with(".MI") {
                "EUR"
            } else {
                "USD"
            };
            let currency = entry.get("currency").and_then(|s| s.as_str()).unwrap_or(default_currency).to_string();
            // Map watchlist exchange names to our internal exchange codes
            let ticker_type = entry.get("type").and_then(|t| t.as_str()).unwrap_or("");
            let is_leveraged = ticker_type == "leveraged_etp" || ticker_type == "inverse_etp"
                || entry.get("leveraged").and_then(|v| v.as_bool()).unwrap_or(false);
            let exchange = match exchange.as_str() {
                "LSE" => {
                    if symbol.ends_with(".L") && is_leveraged {
                        "LSEETF".to_string()
                    } else {
                        "LSE".to_string()
                    }
                }
                "NASDAQ" | "NYSE" | "AMEX" => "SMART".to_string(),
                other => other.to_string(),
            };
            result.push((symbol, exchange, currency));
        }
        result
    }

    /// Get the watchlist file — always unified across all 6 markets.
    /// Ouroboros generates a single ranked watchlist every 15 minutes,
    /// with LSE leveraged ETPs boosted to the top.
    fn watchlist_for_mode(_mode: SessionMode) -> &'static str {
        "config/active_watchlist.json"
    }

    /// Rotate subscriptions from the unified watchlist.
    /// Rotate only the dark horse subscription slots (unusual movers: RVOL spike, gap, volume outlier).
    /// Delegates to full watchlist rotation since the ranked watchlist already prioritizes dark horses.
    /// The dark horse slots are the tail-end of the watchlist (beyond core slots).
    fn rotate_dark_horse_slots(&mut self) {
        eprintln!("SCANNER_80_20: dark horse slot rotation → delegating to full watchlist rotation");
        self.last_dark_horse_rotation_ns = self.now_ns;
        self.rotate_subscriptions_from_watchlist();
    }

    /// Called on mode transitions and every 15 minutes during ACTIVE mode.
    /// Reads a single active_watchlist.json covering ALL 6 markets.
    /// IBKR paper trading max: 100 simultaneous market data lines.
    /// LSE leveraged/inverse ETPs are ALWAYS in the first 12 slots.
    fn rotate_subscriptions_from_watchlist(&mut self) {
        let mode = self.session_mgr.mode();
        if matches!(mode, SessionMode::Dark | SessionMode::Carry) {
            return; // No rotation in non-trading modes
        }

        // IBKR paper: 100 simultaneous market data lines (the hard max).
        // We use all 100 slots — Ouroboros ranks the best 100 across all open markets.
        let max_tickers: usize = 100;

        let primary_path = Self::watchlist_for_mode(mode);
        let mut watchlist_tickers = Self::load_watchlist_tickers(primary_path, max_tickers);

        // If watchlist is empty, fall back to static market_config (all markets combined)
        if watchlist_tickers.is_empty() {
            eprintln!("WATCHLIST_ROTATE: {} empty/missing, using static fallback", primary_path);
            // Combine all static tickers: ISA core + TSE + HKEX + XETRA + Euronext + US
            let static_tickers = self.market_config.all_markets_tickers();
            let new_ticker_ids: Vec<TickerId> = static_tickers
                .iter()
                .take(max_tickers)
                .map(|sym| self.universe.intern.intern(sym))
                .collect();
            self.execute_subscription_rotation(&new_ticker_ids, mode);
            return;
        }

        // ISA core priority REMOVED (2026-03-23): All tickers ranked equally by ticker_selector.
        // Previously prepended ISA core ETPs during LSE hours. Now we scan all tickers
        // without bias — ticker_selector's scoring handles prioritization.

        // Truncate to IBKR max (100 lines)
        watchlist_tickers.truncate(max_tickers);

        // P21-FX: Filter out exchanges where we have NO market data subscription.
        // User's IBKR subscriptions: LSE, Euronext, XETRA, HKEX, TSE, KRX, US, IDEALPRO.
        // Missing: ASX (Australia), SGX (Singapore).
        let unsupported_exchanges = ["ASX", "SGX"];
        let watchlist_tickers: Vec<_> = watchlist_tickers.into_iter()
            .filter(|(symbol, exchange, _)| {
                if unsupported_exchanges.contains(&exchange.as_str()) {
                    eprintln!("WATCHLIST_FILTER: skipping {} (no {} market data subscription)", symbol, exchange);
                    false
                } else {
                    true
                }
            })
            .collect();

        // Register new tickers in universe + broker
        let mut new_ticker_ids = Vec::new();
        let mut dynamic_registered = 0u32;
        for (symbol, exchange, currency) in &watchlist_tickers {
            let tid = self.universe.register(symbol, crate::universe::UniverseClass::Vanguard);
            self.bar_history.entry(tid).or_insert_with(|| BarHistory::new(300));
            self.thompson_sampler.register(tid);

            // P21-FX: Cache the currency for this ticker (used in process_tick FX conversion)
            self.ticker_currencies.insert(tid, currency.clone());

            // Dynamically register contract if broker doesn't know about it
            if !self.broker.has_contract(&tid) {
                if self.broker.register_dynamic_contract(tid, symbol, exchange, currency) {
                    dynamic_registered += 1;
                }
            }
            new_ticker_ids.push(tid);
        }

        if dynamic_registered > 0 {
            eprintln!(
                "WATCHLIST_ROTATE: registered {} new dynamic contracts",
                dynamic_registered
            );
        }

        eprintln!(
            "WATCHLIST_ROTATE: {} mode=ACTIVE tickers={}/{} (from {})",
            if self.last_watchlist_rotation_ns == 0 { "INITIAL" } else { "REFRESH" },
            new_ticker_ids.len(), max_tickers, primary_path
        );

        self.execute_subscription_rotation(&new_ticker_ids, mode);
        self.last_watchlist_rotation_ns = self.now_ns;
    }

    /// Execute the actual subscription rotation (unsub old, sub new).
    fn execute_subscription_rotation(&mut self, new_ticker_ids: &[TickerId], mode: SessionMode) {
        let currently_subscribed = self.subscription_manager.active_ticker_ids();

        // Calculate which tickers to unsubscribe
        let to_unsubscribe: Vec<TickerId> = currently_subscribed
            .iter()
            .filter(|tid| !new_ticker_ids.contains(tid))
            .copied()
            .collect();

        if !to_unsubscribe.is_empty() {
            let unsub_count = self.broker.unsubscribe_l1_batch(&to_unsubscribe);
            eprintln!("SUBSCRIBE: unsubscribed {} L1 feeds", unsub_count);
        }

        // Update subscription manager
        let activated = self.subscription_manager.rotate_tickers(new_ticker_ids.to_vec(), self.now_ns);

        // Subscribe to new tickers
        if !activated.is_empty() {
            let sub_count = self.broker.subscribe_l1_batch(&activated);
            eprintln!(
                "SUBSCRIBE: {} active, {} L1 feeds for {}",
                activated.len(), sub_count, mode
            );
        }
    }

    /// P4-B: Verify hardening invariants. Escalate to REDUCE on failure.
    fn verify_hardening_invariants(&mut self) {
        // H20: Paper mode (enforced at compile time in main.rs, belt-and-suspenders here)
        // H34: Max positions check
        let max_pos = self.config.risk.max_positions;
        if self.positions.len() > max_pos as usize {
            eprintln!(
                "HARDENING: H34 VIOLATION — {} positions exceeds max {}",
                self.positions.len(),
                max_pos
            );
            if self.arbiter.regime < RiskRegime::Reduce {
                self.arbiter.regime = RiskRegime::Reduce;
                self.telemetry.regime_escalations.inc();
            }
        }

        // P4-B: Panic guard check
        if self.panic_guard.has_panicked() {
            eprintln!(
                "HARDENING: PANIC DETECTED — {} panics, escalating to HALT",
                self.panic_guard.panic_count()
            );
            self.arbiter.regime = RiskRegime::Halt;
            self.halt_from_watchdog = false; // Not watchdog-caused — block auto-recovery
            self.telemetry.regime_escalations.inc();
        }

        // P4-B: Circuit breaker check
        if self.broker_circuit_breaker.is_tripped(self.now_ns) {
            eprintln!(
                "HARDENING: CIRCUIT BREAKER TRIPPED — {} errors, escalating to REDUCE",
                self.broker_circuit_breaker.error_count()
            );
            if self.arbiter.regime < RiskRegime::Reduce {
                self.arbiter.regime = RiskRegime::Reduce;
                self.telemetry.regime_escalations.inc();
            }
        }

        // P4-B: Watchdog expiry check (no ticks for >120s → HALT)
        // PAPER VALIDATION: Skip watchdog halt in simulation mode — bridge timeouts
        // from Claude curator latency should not halt the engine.
        if self.tick_watchdog.is_expired(self.now_ns) {
            if self.simulation_mode {
                self.tick_watchdog.feed(self.now_ns);
            } else if self.current_trading_session == TradingSession::Closed {
                // Market closed — no ticks expected. Feed the watchdog to prevent
                // spurious expirations and avoid permanent HALT during off-hours.
                self.tick_watchdog.feed(self.now_ns);
                eprintln!(
                    "TICK_WATCHDOG: market closed (session=Closed) — resetting watchdog, no escalation"
                );
            } else {
                eprintln!(
                    "HARDENING: TICK WATCHDOG EXPIRED — no ticks for >120s ({} expirations), escalating to HALT",
                    self.tick_watchdog.expirations()
                );
                self.arbiter.regime = RiskRegime::Halt;
                self.halt_from_watchdog = true;
                self.telemetry.regime_escalations.inc();
                self.write_wal(WalPayload::RiskStateChange {
                    from: "any".to_string(),
                    to: "Halt".to_string(),
                    trigger: "tick_watchdog_expired".to_string(),
                });
            }
        }
    }

    /// P3-B: Add a tick to the Apex snapshot buffer for the ticker.
    /// Returns true if a 60-second window completed (ready for ApexScout evaluation).
    pub fn record_apex_snapshot(&mut self, tick: &MarketTick) -> bool {
        const SNAPSHOT_WINDOW_NS: u64 = 60 * 1_000_000_000; // 60 seconds

        let tid = tick.ticker_id;
        let now_ns = self.now_ns;

        // Initialize snapshot buffer if needed
        self.apex_snapshots.entry(tid).or_default();
        self.last_snapshot_ns.entry(tid).or_insert(now_ns);

        // Check if 60s window has elapsed
        let last_snap_ns = self.last_snapshot_ns[&tid];
        let time_elapsed = now_ns.saturating_sub(last_snap_ns);

        // Create snapshot record (use current prices as OHLCV approximation)
        let snapshot = serde_json::json!({
            "open": tick.last,
            "high": tick.last.max(self.bar_history.get(&tid).map(|h| h.last_high()).unwrap_or(tick.last)),
            "low": tick.last.min(self.bar_history.get(&tid).map(|h| h.last_low()).unwrap_or(tick.last)),
            "close": tick.last,
            "volume": tick.volume,
            "timestamp_ns": now_ns,
        });

        // Add to buffer
        let buffer = self.apex_snapshots.entry(tid).or_default();
        buffer.push_back(snapshot);
        if buffer.len() > 500 {
            buffer.pop_front();
        }

        // Check if 60s window completed
        if time_elapsed >= SNAPSHOT_WINDOW_NS {
            self.last_snapshot_ns.insert(tid, now_ns);
            return true;
        }

        false
    }

    /// Get accumulated Apex snapshots for a ticker (returns cloned for thread safety).
    pub fn get_apex_snapshots(&self, ticker_id: TickerId) -> Vec<serde_json::Value> {
        self.apex_snapshots
            .get(&ticker_id)
            .map(|buf| buf.iter().cloned().collect())
            .unwrap_or_default()
    }

    /// Clear Apex snapshots after evaluation.
    pub fn clear_apex_snapshots(&mut self, ticker_id: TickerId) {
        if let Some(buf) = self.apex_snapshots.get_mut(&ticker_id) {
            buf.clear();
        }
    }

    /// P3-B: Process an Apex tick through HotScanner for momentum/volume signals.
    /// Returns a signal candidate if the scanner score exceeds threshold.
    pub fn process_apex_tick(&mut self, tick: &MarketTick) -> Option<crate::scanner::SignalCandidate> {
        let atr = self.current_atr(tick.ticker_id);
        self.hot_scanner.on_tick(
            tick.ticker_id,
            tick.last,
            0, // Volume from bar data not available in tick — use 0 for now
            atr,
            self.now_ns,
        )
    }

    /// P2-C: Check if a new trading day has started and perform daily reset.
    /// Resets: consecutive_stop_losses, daily_high_watermark, daily_pnl.
    pub fn maybe_daily_reset(&mut self, current_date: &str) {
        if self.last_trading_date.as_deref() == Some(current_date) {
            return;
        }
        let previous_equity = self.portfolio.equity;
        self.portfolio.consecutive_stop_losses = 0;
        self.portfolio.daily_pnl = 0.0;
        self.portfolio.high_water_mark = self.portfolio.equity;
        // N0a: Reset daily trade count on new trading day.
        self.portfolio.daily_trade_count = 0;
        self.write_wal(WalPayload::DailyReset {
            date: current_date.to_string(),
            previous_equity,
            new_equity: self.portfolio.equity,
        });
        // P16: Reset liquidation defense daily state.
        self.liquidation_defense.daily_reset(self.portfolio.equity);
        eprintln!(
            "DAILY_RESET: date={current_date}, equity={:.2}",
            self.portfolio.equity
        );
        self.last_trading_date = Some(current_date.to_string());
    }

    /// Handle IBKR error codes (H43, H44, H46).
    pub fn handle_ibkr_error(&mut self, code: i32, message: &str) {
        match code {
            1100 => {
                // Connectivity lost (H43) → immediate HALT
                eprintln!("IBKR ERROR 1100: {message} → HALT");
                self.arbiter.regime = RiskRegime::Halt;
                self.halt_from_watchdog = false; // Not watchdog-caused — block auto-recovery
                self.write_wal(WalPayload::RiskStateChange {
                    from: "any".to_string(),
                    to: "Halt".to_string(),
                    trigger: format!("IBKR error 1100: {message}"),
                });
            }
            1102 => {
                // Reconnected (H44) → reconcile then auto-clear Halt if safe
                eprintln!("IBKR ERROR 1102: {message} → reconcile + resubscribe");
                let _ = self.reconcile();
                // FIX: Re-subscribe to all market data after reconnect.
                // IBKR drops all subscriptions on disconnect (Error 1100).
                // Without resubscribing, engine receives zero ticks after reconnect.
                // Note: secdef farms should already be ready since this is a reconnect
                // (farms persist across client reconnections), so no delay needed here.
                let resub_count = self.broker.resubscribe_all();
                eprintln!("IBKR 1102: resubscribed to {resub_count} market data streams");
                // P21-AUTO: Clear Halt from broker disconnect — broker is back
                if self.arbiter.regime == RiskRegime::Halt
                    && !self.broker_health.should_halt(self.now_ns)
                    && !self.liquidation_defense.should_halt()
                    && !self.panic_guard.has_panicked()
                {
                    eprintln!("REGIME_RECOVERY: Halt → Normal (broker reconnected, reconcile clean)");
                    self.arbiter.regime = RiskRegime::Normal;
                    self.halt_from_watchdog = false;
                    self.write_wal(WalPayload::RiskStateChange {
                        from: "Halt".to_string(),
                        to: "Normal".to_string(),
                        trigger: "broker_reconnected_1102".to_string(),
                    });
                }
            }
            321 => {
                // Pacing violation (H46) → 5s backoff
                eprintln!("IBKR ERROR 321: {message} → 5s backoff");
                // Backoff handled by broker adapter's BackoffState
            }
            _ => {
                eprintln!("IBKR ERROR {code}: {message}");
            }
        }
    }

    pub fn process_broker_event(&mut self, ev: &BrokerEvent) {
        match ev {
            BrokerEvent::Fill {
                order_id,
                ticker_id,
                filled_qty,
                remaining_qty,
                price,
                exec_id,
                commission,
            } => {
                // N0f: Capture spread at fill time for cost attribution.
                let fill_spread_pct = {
                    let last_tick = self.last_prices.get(ticker_id);
                    // Use stored bid/ask if available, otherwise 0.0
                    if let Some(_) = last_tick {
                        // We don't have separate bid/ask stored per-tick in last_prices,
                        // so use 0.0 for now. Full implementation needs bid/ask cache.
                        0.0_f64
                    } else {
                        0.0
                    }
                };
                // Determine if this is a buy or sell fill from tracked orders.
                let fill_side = if order_id.starts_with("exit-") {
                    "Sell".to_string()
                } else {
                    "Buy".to_string()
                };
                self.write_wal(WalPayload::FillEvent {
                    order_id: order_id.clone(),
                    ticker_id: ticker_id.0,
                    filled_qty: *filled_qty,
                    remaining_qty: *remaining_qty,
                    price: *price,
                    exec_id: exec_id.clone(),
                    commission: *commission,
                    spread_at_fill_pct: fill_spread_pct,
                    side: fill_side,
                });
                // P3-C: Update Executioner lifecycle on fill
                self.executioner.record_fill(order_id, *filled_qty, self.now_ns);
                if *remaining_qty == 0 {
                    // P4-A: Record completed fill in telemetry
                    self.telemetry.orders_filled.inc();
                    // P6-C: Record ISA deposit for buy fills
                    let fill_value = *price * *filled_qty as f64;
                    self.isa_gate.record_deposit(fill_value);
                    // P11: Record sector position on fill.
                    self.sector_tracker.record_position(*ticker_id, fill_value);
                    // P16: Record ISA deposit in liquidation defense.
                    self.liquidation_defense.record_deposit(fill_value);
                    // P17: Record fill success in broker health monitor.
                    self.broker_health.record_fill_success(self.now_ns);
                    // P18: Record WAL event in compressor.
                    self.wal_compressor.record_event();
                    // AUDIT-FIX: Use ATR-based stop (1.5× ATR), fallback to 5%
                    let atr_val = self.bar_history.get(ticker_id).map(|h| h.atr(14)).unwrap_or(0.0);
                    let stop_pct = if atr_val > 0.0 && *price > 0.0 {
                        (1.5 * atr_val / *price).clamp(0.01, 0.10)
                    } else {
                        0.05
                    };
                    let stop = initial_stop_price(*price, stop_pct);
                    let pos = PositionState {
                        entry_timestamp_ns: self.now_ns,
                        avg_entry: *price,
                        unrealized_pnl: 0.0,
                        realized_pnl: 0.0,
                        highest_high: *price,
                        stop_price: stop,
                        total_commission: *commission,
                        qty: *filled_qty,
                        ticker_id: *ticker_id,
                        trailing_rung: 0,
                        state: OrderState::ExitRegistered,
                        origin_order_id: order_id.clone(),
                        is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: String::new(),  // Not available during WAL replay
                active_trading_ticks: 0,
                    };
                    self.portfolio.add_position(pos.clone());
                    self.positions.insert(*ticker_id, pos);
                    // N0a: Increment daily trade count for live entry fills.
                    self.portfolio.daily_trade_count += 1;
                }
            }
            BrokerEvent::Ack {
                order_id,
                ibkr_order_id,
                status,
                ..
            } => {
                self.write_wal(WalPayload::BrokerAck {
                    order_id: order_id.clone(),
                    status: format!("{:?}", status),
                    ibkr_order_id: *ibkr_order_id,
                });
                // P3-C: Update Executioner lifecycle on ack
                self.executioner.update_lifecycle(
                    order_id,
                    OrderLifecycle::Acknowledged,
                    self.now_ns,
                );
            }
            BrokerEvent::Disconnected => {
                // P17: Record disconnect in broker health monitor.
                self.broker_health.record_disconnect(self.now_ns);
                self.handle_ibkr_error(1100, "Broker disconnected");
            }
            BrokerEvent::Connected { next_valid_id } => {
                eprintln!("Broker connected, next_valid_id={next_valid_id}");
                // P17: Record heartbeat (reconnect) in broker health monitor.
                self.broker_health.record_heartbeat(self.now_ns);
                self.broker_health.reset_reconnect();
                self.handle_ibkr_error(1102, "Broker reconnected");
            }
        }
    }

    pub fn write_wal(&mut self, payload: WalPayload) {
        if let Some(ref mut wal) = self.wal {
            let event = make_wal_event(self.now_ns, payload);
            if let Err(e) = wal.append(&event) {
                // P3-B1.5: WAL write failure → escalate to HALT (Book 45).
                // Lost events = incomplete audit trail = unsafe to continue trading.
                eprintln!("WAL_WRITE_FAIL: {} — escalating to HALT", e);
                self.arbiter.regime = RiskRegime::Halt;
            }
        }
    }

    /// Check if it's time for periodic reconciliation (every 5 min).
    pub fn should_reconcile(&self) -> bool {
        let interval_ns = self.config.reconciliation.interval_secs * 1_000_000_000;
        self.now_ns >= self.last_reconcile_ns + interval_ns
    }

    /// Write hourly state hash to WAL (H85).
    pub fn maybe_write_state_hash(&mut self) {
        let one_hour_ns: u64 = 3_600_000_000_000;
        if self.now_ns < self.last_state_hash_ns + one_hour_ns {
            return;
        }
        // Compute simple state hash: positions count + equity + regime
        let hash = format!(
            "positions={},equity={:.2},regime={:?},orders={}",
            self.positions.len(),
            self.portfolio.equity,
            self.arbiter.regime,
            self.tracked_orders.len(),
        );
        let checksum = crc32fast::hash(hash.as_bytes());
        eprintln!("STATE_HASH: {hash} → crc32={checksum:#010x}");

        // Build per-position snapshot for Google Sheets Open_Positions + per-ticker unrealised P&L
        let mut open_positions_json = Vec::new();
        let mut total_unrealized_pnl = 0.0f64;
        for (&tid, pos) in &self.positions {
            let symbol = self.broker.symbol_for(tid)
                .unwrap_or_else(|| format!("T{}", tid.0));
            let current_price = self.last_prices.get(&tid).copied().unwrap_or(pos.avg_entry);
            let unrealized = (current_price - pos.avg_entry) * pos.qty as f64;
            total_unrealized_pnl += unrealized;
            open_positions_json.push(serde_json::json!({
                "ticker_id": tid.0,
                "symbol": symbol,
                "qty": pos.qty,
                "entry_price": (pos.avg_entry * 10000.0).round() / 10000.0,
                "current_price": (current_price * 10000.0).round() / 10000.0,
                "unrealized_pnl": (unrealized * 100.0).round() / 100.0,
                "rung": pos.trailing_rung,
                "stop_price": (pos.stop_price * 10000.0).round() / 10000.0,
                "highest_high": (pos.highest_high * 10000.0).round() / 10000.0,
                "exchange": "",
            }));
        }

        // Write state hash as WAL event (H85) — now includes per-position data + unrealised P&L
        self.write_wal(WalPayload::StateSnapshot {
            portfolio_json: format!(
                "{{\"positions\":{},\"high_water\":{:.2},\"unrealized_pnl\":{:.2}}}",
                self.positions.len(),
                self.portfolio.high_water_mark,
                total_unrealized_pnl
            ),
            equity: self.portfolio.equity,
            high_water: self.portfolio.high_water_mark,
            hash: format!("{checksum:#010x}"),
            open_positions: open_positions_json,
        });

        self.last_state_hash_ns = self.now_ns;
    }

    /// P15: Check for pending split events and apply adjustments.
    ///
    /// BLOCKED (R1): Cannot uncomment split adjustment logic until:
    ///   1. ibkr_broker.rs emits SplitEvent when contractDetailsEnd detects
    ///      changed multiplier/conId (IBKR callback integration needed).
    ///   2. This loop must be refactored to collect tickers first, then mutate
    ///      positions (borrow checker: can't mutate while iterating &self.positions).
    ///
    /// P0 SAFETY: Without this, a 1:10 reverse split causes 10x notional exposure.
    /// Priority: Wire before going live with any stock-split-prone underlyings.
    pub fn check_pending_splits(&mut self) {
        for (&tid, pos) in &self.positions {
            if self.split_handler.was_processed(tid, self.now_ns) {
                continue;
            }
            // TODO(R1): When IBKR split events are wired, refactor to:
            //   1. Collect pending splits into Vec<(TickerId, SplitEvent)>
            //   2. After iteration, for each (tid, event):
            //      let pos = self.positions.get_mut(&tid).unwrap();
            //      let adj = self.split_handler.apply_split(&event, pos.qty, pos.avg_entry, pos.stop_price);
            //      pos.qty = adj.new_qty as u32;
            //      pos.avg_entry = adj.new_entry_price;
            //      pos.stop_price = adj.new_stop_price;
            //      self.split_handler.record_processed(event);
            let _ = pos.qty; // Position exists, no split detected yet
        }
    }

    /// P2-3.8: Emergency halt on prolonged broker disconnect with open positions.
    /// If broker is disconnected for >10 seconds AND there are filled positions,
    /// escalate to HALT regime and cancel all tracked orders.
    pub fn check_broker_disconnect_halt(&mut self) {
        if !self.broker.is_connected() && self.portfolio.filled_count() > 0 {
            if self.broker_disconnect_ns == 0 {
                self.broker_disconnect_ns = self.now_ns;
            }
            let disconnect_secs = (self.now_ns.saturating_sub(self.broker_disconnect_ns)) / 1_000_000_000;
            if disconnect_secs > self.config.hardening.broker.broker_disconnect_escalate_secs
                && self.arbiter.regime < RiskRegime::Halt
            {
                eprintln!(
                    "BROKER_HALT: Disconnected {}s with {} open positions — escalating to HALT",
                    disconnect_secs, self.portfolio.filled_count()
                );
                self.arbiter.regime = RiskRegime::Halt;
                // Cancel all tracked orders
                for oid in &self.tracked_orders.clone() {
                    let _ = self.broker.cancel_order(oid);
                }
            }
        } else {
            self.broker_disconnect_ns = 0; // Reset on reconnect
        }
    }

    /// SC-01: Graceful shutdown sequence.
    /// 1. Cancel all pending orders
    /// 2. Flatten all open positions (market sell)
    /// 3. Write SystemShutdown WAL event
    ///    Note: fill-wait loop runs in main.rs (requires IbkrBroker-specific polling).
    pub fn shutdown(&mut self) {
        eprintln!("ENGINE: Shutting down (SC-01 graceful)...");

        // Step 1: Cancel all pending orders
        for oid in &self.tracked_orders.clone() {
            let _ = self.broker.cancel_order(oid);
        }

        // Step 2: Flatten all open positions via market sell
        let position_tickers: Vec<(TickerId, u32)> = self
            .portfolio
            .positions()
            .iter()
            .map(|(&tid, pos)| (tid, pos.qty))
            .collect();
        let positions_flattened = position_tickers.len() as u32;

        for (tid, qty) in &position_tickers {
            let order_id = uuid::Uuid::now_v7().to_string();
            eprintln!("ENGINE: Flattening {:?} ({} shares) {}", tid, qty,
                if self.simulation_mode { "[SIMULATED]" } else { "[LIVE]" });
            if !self.simulation_mode {
                // Only submit real orders in live mode
                // P2-#10: Use 5% below last bid (was hardcoded 0.01 penny limit).
                let bid = self.last_prices.get(tid).copied().unwrap_or(0.01);
                let emergency_limit = (bid * 0.95).max(0.01);
                let _ = self.broker.submit_order(&order_id, *tid, OrderSide::Sell, *qty, emergency_limit);
            }
            // In simulation mode, positions are just removed from self.positions
        }

        // Step 3: Write SystemShutdown WAL event
        self.write_wal(WalPayload::SystemShutdown {
            positions_flattened,
            pending_fills_waited_secs: 0, // Actual wait happens in main.rs
        });

        // P0-1.8: Persist bar_history to disk for crash recovery.
        self.save_bar_history();

        self.startup_complete = false;
        eprintln!(
            "ENGINE: Shutdown complete. Flattened {} positions.",
            positions_flattened
        );
    }

    /// P0-1.8: Save bar_history to JSON file for crash recovery.
    /// Written on shutdown, restored on startup. 1-hour TTL (file timestamp check).
    pub fn save_bar_history(&self) {
        let path = std::path::Path::new("/app/data/bar_history.json");
        // Serialize as HashMap<String, BarHistory> (TickerId → string key for JSON compat)
        let serializable: std::collections::HashMap<String, &BarHistory> = self
            .bar_history
            .iter()
            .map(|(tid, bh)| (tid.0.to_string(), bh))
            .collect();
        match serde_json::to_string(&serializable) {
            Ok(json) => {
                if let Err(e) = std::fs::write(path, json.as_bytes()) {
                    eprintln!("WARNING: Failed to save bar_history: {e}");
                } else {
                    eprintln!("BAR_HISTORY: Saved {} tickers to {}", serializable.len(), path.display());
                }
            }
            Err(e) => eprintln!("WARNING: Failed to serialize bar_history: {e}"),
        }
    }

    /// P0-1.8: Restore bar_history from JSON file (if fresh — <1h old).
    pub fn restore_bar_history(&mut self) {
        let path = std::path::Path::new("/app/data/bar_history.json");
        if !path.exists() {
            eprintln!("BAR_HISTORY: No saved file found, starting fresh");
            return;
        }
        // Check file age — 1 hour TTL
        match std::fs::metadata(path) {
            Ok(meta) => {
                if let Ok(modified) = meta.modified() {
                    if let Ok(age) = std::time::SystemTime::now().duration_since(modified) {
                        if age.as_secs() > 3600 {
                            eprintln!(
                                "BAR_HISTORY: File is {}s old (>3600s TTL), ignoring stale data",
                                age.as_secs()
                            );
                            return;
                        }
                    }
                }
            }
            Err(e) => {
                eprintln!("BAR_HISTORY: Cannot stat file: {e}");
                return;
            }
        }
        // Read and deserialize
        match std::fs::read_to_string(path) {
            Ok(json) => {
                match serde_json::from_str::<std::collections::HashMap<String, BarHistory>>(&json) {
                    Ok(loaded) => {
                        let mut restored = 0u32;
                        for (key, bh) in loaded {
                            if let Ok(tid_u32) = key.parse::<u32>() {
                                let tid = TickerId(tid_u32);
                                let bar_count = bh.closes.len();
                                self.bar_history.insert(tid, bh);
                                restored += 1;
                                if bar_count > 0 {
                                    eprintln!(
                                        "BAR_HISTORY: Restored ticker {} with {} bars",
                                        tid_u32, bar_count
                                    );
                                }
                            }
                        }
                        eprintln!("BAR_HISTORY: Restored {} tickers from disk", restored);
                    }
                    Err(e) => eprintln!("BAR_HISTORY: Failed to deserialize: {e}"),
                }
            }
            Err(e) => eprintln!("BAR_HISTORY: Failed to read file: {e}"),
        }
    }

    /// P21: Apply subscription rotation for a mode transition.
    /// Unified architecture: Active mode subscribes to 100 tickers across all markets.
    /// Dark mode suspends all subscriptions.
    #[allow(deprecated)]
    fn apply_mode_subscription_rotation(&mut self, new_mode: SessionMode) {
        match new_mode {
            SessionMode::Active | SessionMode::ModeA | SessionMode::ModeB
            | SessionMode::ModeBPlus | SessionMode::ModeC | SessionMode::Auction => {
                eprintln!("SUBSCRIBE: Mode transition → ACTIVE (all 6 markets, 100 tickers, 15-min refresh)");
                self.rotate_subscriptions_from_watchlist();
                // Unfreeze any carry stops when entering active trading
                self.carry_manager.unfreeze_all_stops();
            }
            SessionMode::Dark => {
                eprintln!("SUBSCRIBE: Mode transition → Dark (21:00-23:00 London — suspend all)");

                // Get all tickers currently subscribed
                let currently_subscribed = self.subscription_manager.active_ticker_ids();

                if !currently_subscribed.is_empty() {
                    eprintln!(
                        "SUBSCRIBE: unsubscribing {} tickers (Dark mode active)",
                        currently_subscribed.len()
                    );
                    let unsub_count = self.broker.unsubscribe_l1_batch(&currently_subscribed);
                    eprintln!(
                        "SUBSCRIBE: unsubscribed {} L1 feeds",
                        unsub_count
                    );
                }

                // Deactivate all in subscription manager
                let activated = self.subscription_manager.rotate_tickers(vec![], self.now_ns);
                eprintln!("SUBSCRIBE: Dark mode — all subscriptions suspended ({})", activated.len());
            }
            SessionMode::Carry => {
                // Carry: positions held during Dark hours, keep monitoring those specific tickers
                self.carry_manager.freeze_all_stops(self.now_ns);
                eprintln!("MODE: CARRY — frozen stops, keeping position tickers subscribed");
            }
        }
    }
}

/// Round price to valid LSE tick size (H65).
/// Under £1: £0.001 increments. Over £1: £0.01 increments.
pub fn round_to_tick_size(price: f64, config: &EngineConfig) -> f64 {
    let tick = if price < 1.0 {
        config.execution.tick_size_under_1
    } else {
        config.execution.tick_size_over_1
    };
    (price / tick).floor() * tick
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn test_tick_size_rounding() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent")
            .join("config");
        let config = EngineConfig::load(&config_dir).expect("load");
        // Over £1: round to £0.01
        assert!((round_to_tick_size(10.567, &config) - 10.56).abs() < 0.001);
        assert!((round_to_tick_size(1.005, &config) - 1.0).abs() < 0.001);
        // Under £1: round to £0.001
        assert!((round_to_tick_size(0.5678, &config) - 0.567).abs() < 0.0001);
    }

    #[test]
    fn test_line_budget_valid() {
        let lb = LineBudget::new(30, 50, 20);
        assert!(lb.is_some());
        let lb = lb.expect("valid");
        assert_eq!(lb.total(), 100);
        assert_eq!(lb.remaining(), 0);
    }

    #[test]
    fn test_line_budget_exceeds_100_returns_none() {
        assert!(LineBudget::new(50, 40, 20).is_none()); // 110 > 100
        assert!(LineBudget::new(100, 1, 0).is_none());  // 101 > 100
        assert!(LineBudget::new(34, 34, 33).is_none()); // 101 > 100
    }

    #[test]
    fn test_line_budget_boundary() {
        // Exactly 100
        assert!(LineBudget::new(100, 0, 0).is_some());
        assert!(LineBudget::new(0, 0, 0).is_some());
        // Under 100
        assert!(LineBudget::new(33, 33, 34).is_some()); // 100
        // Over 100
        assert!(LineBudget::new(34, 34, 33).is_none()); // 101
    }

    #[test]
    fn test_line_budget_default() {
        let lb = LineBudget::default();
        assert_eq!(lb.carry, 30);
        assert_eq!(lb.active, 50);
        assert_eq!(lb.scan, 20);
        assert_eq!(lb.total(), 100);
        assert_eq!(lb.remaining(), 0);
    }

    #[test]
    fn test_line_budget_remaining() {
        let lb = LineBudget::new(10, 20, 30).expect("valid");
        assert_eq!(lb.total(), 60);
        assert_eq!(lb.remaining(), 40);
    }

    #[test]
    fn test_bar_history_atr() {
        let mut bh = BarHistory::new(100);
        // Push 20 bars with known values
        for i in 0..20 {
            let base = 100.0 + i as f64;
            bh.push(base + 1.0, base - 1.0, base, 1000);
        }
        let atr = bh.atr(14);
        // TR = high - low = 2.0 for each bar (when close-to-close is ~1.0, high-low dominates)
        assert!(atr > 1.5 && atr < 3.0, "ATR should be ~2.0, got {atr}");
    }
}
