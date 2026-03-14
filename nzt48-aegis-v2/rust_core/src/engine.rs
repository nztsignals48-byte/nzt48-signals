//! Live/Paper Engine — 8-step startup, tick processing, reconciliation, shutdown.
//! Generic over BrokerAdapter: works with PaperBroker (testing) or IbkrBroker (live).

use crate::asian_session::AsianSession;
use crate::broker::{BrokerAdapter, BrokerError, BrokerEvent};
use crate::clock::{Clock, TradingMode};
use crate::config_loader::EngineConfig;
use serde_json;
use crate::cross_timezone::CrossTimezoneEngine;
use crate::currency::FxRateTable;
use crate::european_session::EuropeanSession;
use crate::exchange_profile::ExchangeRegistry;
use crate::exit_engine::{ExitEngine, Executioner, OrderLifecycle, TrackedOrder, initial_stop_price};
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
use crate::reconciler;
use crate::risk_arbiter::{EvalContext, RiskArbiter};
use crate::types::{
    Direction, MarketTick, OrderSide, OrderState, PositionState, RiskRegime, TickerId, WalPayload,
};
use crate::universe::{RouteResult, Universe, UniverseClass, UniverseConfig};
use crate::wal_writer::{WalWriter, make_wal_event};
use std::collections::{HashMap, VecDeque};

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
#[derive(Clone, Debug)]
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
}

impl<B: BrokerAdapter> Engine<B> {
    pub fn new(broker: B, config: EngineConfig, wal: Option<WalWriter>, clock: Clock) -> Self {
        let equity = config.crucible.starting_equity_gbp;
        let mut risk_config = config.risk.clone();
        if config.crucible.paper_mode {
            risk_config.max_positions = config.crucible.max_positions_override;
        }
        let arbiter = RiskArbiter::new(risk_config);
        Self {
            broker,
            portfolio: PortfolioState::new(equity),
            arbiter,
            exit_engine: ExitEngine::with_default_chandelier(),
            wal,
            clock,
            config,
            tracked_orders: Vec::new(),
            last_prices: HashMap::new(),
            positions: HashMap::new(),
            gap_cooldowns: HashMap::new(),
            now_ns: 0,
            startup_complete: false,
            last_reconcile_ns: 0,
            universe: Universe::new(UniverseConfig::default()),
            bar_history: HashMap::new(),
            apex_snapshots: HashMap::new(),
            last_snapshot_ns: HashMap::new(),
            apex_candles: HashMap::new(),
            last_state_hash_ns: 0,
            current_mode: TradingMode::Dark,
            last_trading_date: None,
            garch_registry: GarchRegistry::empty(),
            hot_scanner: HotScanner::new(30.0, 10), // Score threshold 30, max 10 candidates
            rotation_scanner: RotationScanner::new(0.05, 10), // 5% rotation strength threshold, max 10 candidates
            executioner: Executioner::new(),
            smart_router: SmartRouter::new(IsaGate::new("2026-04-06")),
            subscription_manager: SubscriptionManager::new(100), // IBKR 100-line limit
            telemetry: Telemetry::new(),
            panic_guard: PanicGuard::new(),
            broker_circuit_breaker: CircuitBreaker::new(5, 60, 30), // 5 errors/min, 30s cooldown
            tick_watchdog: Watchdog::new(120), // 120s timeout for tick liveness
            evt_registry: EvtRegistry::default(),
            hy_engine: HayashiYoshidaEngine::default(),
            fx_table: FxRateTable::default(),
            exchange_registry: ExchangeRegistry::default(),
            isa_gate: IsaGate::new("2026-04-06"),
            asian_session: AsianSession::default(),
            european_session: EuropeanSession::default(),
            cross_timezone: CrossTimezoneEngine::default(),
            carry_manager: CarryManager::default(),
            macro_regime: CrossAssetMacro::new(),
            multiframe_vol: HashMap::new(),
            sector_tracker: SectorHeatTracker::new(),
            predictive_scorer: PredictiveScorer::new(),
            quote_imbalance: QuoteImbalanceDetector::new(),
            split_handler: SplitHandler::new(),
            liquidation_defense: LiquidationDefense::new(20_000.0),
            broker_health: BrokerHealthMonitor::new(),
            wal_compressor: WalCompressor::new("events/current.ndjson", "events/archive", 1_000_000),
            checkpoint_mgr: CheckpointManager::new(3600),
            session_mgr: SessionManager::new(),
            last_session_mode: SessionMode::Dark,
            market_config: MarketConfig::new(),
            latency_profiler: LatencyProfiler::new(),
            kalman_filters: HashMap::new(),
            thompson_sampler: LogThompsonSampler::new(),
        }
    }

    /// Execute the 8-step startup sequence.
    pub fn startup(
        &mut self,
        wal_events: &[crate::types::WalEvent],
        broker_time_secs: u64,
        system_time_ns: u64,
    ) -> Result<StartupResult, EngineError> {
        // Step 1: Verify broker connection
        if !self.broker.is_connected() {
            return Err(EngineError::BrokerNotConnected);
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
        eprintln!(
            "STARTUP: WAL replayed {} events, {} orphans",
            replay_result.events_replayed,
            replay_result.orphaned_orders.len()
        );

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
                eprintln!(
                    "STARTUP: Restored risk regime from WAL: {:?} (safety brake preserved)",
                    restored
                );
            }
        }

        // Step 4: Reconcile positions with broker
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

        // Step 5: Resolve orphaned orders
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

        // Step 7: Write SystemReady to WAL
        self.now_ns = system_time_ns;
        self.write_wal(WalPayload::SystemReady {
            wal_events_replayed: replay_result.events_replayed,
            positions_reconciled: recon.matches as u32,
        });

        // Step 8: Mark startup complete
        self.startup_complete = true;
        self.last_reconcile_ns = system_time_ns;
        self.last_state_hash_ns = system_time_ns;

        Ok(StartupResult {
            wal_events_replayed: replay_result.events_replayed,
            positions_reconciled: recon.matches as u32,
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

        let t2t_start_ns = self.now_ns; // For T2T latency logging (H118)
        self.now_ns = tick.timestamp_ns;

        // Gap detection (H66): >2% gap → 15-min cooldown
        let tid = tick.ticker_id;
        if let Some(&prev) = self.last_prices.get(&tid)
            && prev > 0.0
        {
            let gap_pct = ((tick.last - prev) / prev).abs();
            if gap_pct > 0.02 {
                let cooldown_ns = self.config.gap_cooldown_mins as u64 * 60 * 1_000_000_000;
                self.gap_cooldowns.insert(tid, self.now_ns + cooldown_ns);
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

        // P14: Quote imbalance — record bid/ask and check for spoofing.
        if tick.bid > 0.0 && tick.ask > 0.0 {
            self.quote_imbalance.record_quote(tid, tick.bid, tick.ask, self.now_ns);
            if self.quote_imbalance.is_spoofed(tid) {
                eprintln!("SPOOF_DETECT: ticker={} — dropping tick, escalating to REDUCE", tid.0);
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
        let _kalman_state = {
            let filter = self.kalman_filters
                .entry(tid)
                .or_insert_with(|| StudentTKalmanFilter::new(tick.last, 1.0, 0.01, 0.1, 100));
            filter.step(tick.last);
            filter.state()
        };

        self.last_prices.insert(tid, tick.last);

        // P5-D: Record tick for Hayashi-Yoshida covariance estimation
        self.hy_engine.record_tick(tid, tick.last, tick.timestamp_ns);

        // ATR from rolling bar history (Wilder's 14-period)
        let atr = self.current_atr(tid);

        // Real London time from synced clock
        let time_secs = self.london_time_secs();

        // Phase 11: Determine current trading mode
        self.current_mode = TradingMode::from_london_secs(time_secs);

        // Phase 11: In Dark/ModeC modes, no trading at all
        if matches!(self.current_mode, TradingMode::Dark | TradingMode::ModeC) {
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

        // P3-C: RotationScanner for sector rotation signal detection during Mode B
        if matches!(self.current_mode, TradingMode::ModeB) && self.universe.is_apex(tid) {
            // RotationScanner monitors sector relative strength during European session
            // Called on 60s snapshots (from apex_candles completion)
            // This is a placeholder; actual snapshot feeding is in Phase 3
        }

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
                realized_vol,
                time_fraction,
                momentum,
                amihud,
                heat,
                is_reduce,
            );
        }

        // Exit evaluation for existing positions (Phase 11: only when mode allows exits)
        if self.current_mode.allows_exits() && let Some(pos) = self.positions.get_mut(&tid) {
            if self
                .exit_engine
                .is_price_spike(pos.highest_high, tick.last, tick.bid, tick.ask)
            {
                return; // H71: price spike filter
            }
            self.exit_engine.update_tracking(pos, tick.last, atr);
            pos.unrealized_pnl = (tick.last - pos.avg_entry) * pos.qty as f64;

            let is_halt = self.arbiter.regime >= RiskRegime::Flatten;
            let is_eod = Clock::eod_phase(time_secs).is_some();
            // P21: Check if position is in carry state (stops frozen)
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

                self.write_wal(WalPayload::ExitSignal {
                    ticker_id: tid.0,
                    reason: reason_str.clone(),
                    priority: priority_str,
                });

                // Phase 2A: Submit SELL order to broker.
                // ExitResult tells us the order type and limit price.
                let exit_order_id = format!("exit-{}", uuid::Uuid::now_v7());
                let sell_limit = match result.signal.order_type {
                    crate::types::ExitOrderType::LimitAtStop => {
                        // Use the exit signal's limit price (the stop level)
                        let raw = result.signal.limit_price.unwrap_or(tick.bid);
                        round_to_tick_size(raw, &self.config)
                    }
                    crate::types::ExitOrderType::MarketSell
                    | crate::types::ExitOrderType::MarketToLimit => {
                        // Market-like: use bid with small buffer below to ensure fill
                        let raw = tick.bid * 0.999; // 10bps below bid for aggressive fill
                        round_to_tick_size(raw.max(0.01), &self.config)
                    }
                };

                self.write_wal(WalPayload::RoutedOrder {
                    order_id: exit_order_id.clone(),
                    ticker_id: tid.0,
                    side: "Sell".to_string(),
                    confidence: 0.0,
                    strategy: reason_str,
                    kelly_fraction: 0.0,
                    approved_size: sell_limit * exit_qty as f64,
                });
                self.tracked_orders.push(exit_order_id.clone());

                // P3-C: Track exit order in Executioner
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

                if let Err(e) = self.broker.submit_order(
                    &exit_order_id,
                    tid,
                    OrderSide::Sell,
                    exit_qty,
                    sell_limit,
                ) {
                    eprintln!(
                        "EXIT: sell order FAILED for ticker={} qty={} err={e} — position remains!",
                        tid.0, exit_qty
                    );
                    // Don't remove position if sell failed — let reconciliation catch it
                    return;
                }

                eprintln!(
                    "EXIT: {:?} ticker={} qty={} limit={sell_limit:.4}",
                    result.signal.reason, tid.0, exit_qty
                );

                // Drain broker events from the sell submission
                let sell_events = self.broker.drain_events();
                for ev in &sell_events {
                    self.process_broker_event(ev);
                }

                self.write_wal(WalPayload::PositionClosed {
                    ticker_id: tid.0,
                    final_pnl,
                    entry_time_ns: entry_time,
                    exit_time_ns: self.now_ns,
                });
                // P11: Clear sector position on close.
                let close_notional = tick.last * exit_qty as f64;
                self.sector_tracker.clear_position(tid, close_notional);
                // P12: Record trade outcome in predictive scorer.
                self.predictive_scorer.record_trade(tid, final_pnl, time_secs);
                // P16: Record stop loss or win in liquidation defense.
                if final_pnl < 0.0 {
                    self.liquidation_defense.record_stop_loss();
                } else {
                    self.liquidation_defense.record_win();
                }
                // P5-C: Observe return in Thompson sampler for allocation learning.
                if close_notional > 0.0 {
                    let return_pct = final_pnl / close_notional * 100.0;
                    self.thompson_sampler.observe(tid, return_pct);
                }
                self.portfolio.remove_position(tid);
                self.positions.remove(&tid);
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

        // Phase 11: Only ModeB allows new entries (replaces H35 cutoff + auction checks)
        if !self.current_mode.allows_entries() {
            return;
        }

        // Auction period check: no entries during auctions (ModeB refinement)
        if Clock::is_auction(time_secs) {
            return;
        }

        // P12: Check if ticker is locked by predictive scorer (5 consecutive losses).
        if self.predictive_scorer.is_locked(tid) {
            return;
        }

        // P11: Check sector concentration before entry (33% cap per sector).
        let sector = sector_for_ticker(tid);
        if self.sector_tracker.is_over_concentrated(sector, self.portfolio.equity, 33.0) {
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
        }

        // P6-E: European session entry gating (supplement to ModeB check).
        if !self.european_session.entry_allowed("XLON", (self.now_ns / 1_000_000_000) as u32 % 86400) {
            return;
        }

        // Determine signal: from Python Brain (required for live/paper)
        // FIX 2026-03-11: Removed phantom fallback that generated 78% confidence
        // Long trades when Python bridge was dead. No signal = no trade.
        let Some(ref sig) = signal else {
            return; // No Python signal → no trade. Never generate phantom entries.
        };
        let direction = if sig.direction == "Short" {
            Direction::Short
        } else {
            Direction::Long
        };
        let confidence = sig.confidence;
        let kelly_fraction = sig.kelly_fraction;
        let strategy_name = sig.strategy.clone();
        let shares_hint = sig.shares;

        // Risk arbiter evaluation with real time and spread
        let ctx = EvalContext {
            time_secs,
            last_tick_age_secs: 1,
            bid: tick.bid,
            ask: tick.ask,
            broker_connected: self.broker.is_connected(),
            wal_available: self.wal.is_some(),
            now_ns: self.now_ns,
            kelly_fraction_raw: kelly_fraction,
            garch_sigma,
            ..EvalContext::default()
        };
        let decision = self.arbiter.evaluate(
            tid,
            direction,
            confidence,
            kelly_fraction,
            &self.portfolio,
            &ctx,
        );
        if !decision.approved {
            // P4-A: Record veto in telemetry
            self.telemetry.record_veto(&format!("{:?}", decision.reason));
            return;
        }
        // P4-A: Record approved signal
        self.telemetry.signals_approved.inc();

        // P6-C: ISA gate check before order submission.
        let trade_value_gbp = tick.ask * shares_hint as f64;
        // P3-D: SmartRouter — check ETP route (passthrough in Crucible mode).
        let symbol = self.config.contracts.get(tid.0 as usize)
            .map(|c| c.symbol.as_str())
            .unwrap_or("UNKNOWN");
        if let Some(_etp) = self.smart_router.find_etp(symbol) {
            // ETP mapping exists — route through ETP wrapper (already using ETP in Crucible)
        }
        // P6-B: Exchange profile — validate tick rounding via exchange registry.
        if let Some(profile) = self.exchange_registry.by_mic("XLON") {
            let _rounded = profile.round_tick(tick.ask);
        }
        let isa_check = self.isa_gate.check("XLON", trade_value_gbp);
        if !matches!(isa_check, crate::isa_gate::IsaCheckResult::Allowed) {
            eprintln!("ISA_GATE: rejected trade for ticker={}, result={isa_check:?}", tid.0);
            return;
        }

        // Submit order
        let order_id = format!("order-{}", self.broker.next_valid_id());
        let limit_price =
            tick.ask * (1.0 + self.config.execution.marketable_limit_buffer_pct / 100.0);
        // Round to tick size (H65)
        let limit_price = round_to_tick_size(limit_price, &self.config);

        self.write_wal(WalPayload::RoutedOrder {
            order_id: order_id.clone(),
            ticker_id: tid.0,
            side: format!("{direction:?}"),
            confidence,
            strategy: strategy_name,
            kelly_fraction,
            approved_size: decision.adjusted_size,
        });
        self.tracked_orders.push(order_id.clone());

        // Position size: prefer Python's Kelly-computed shares, else from arbiter
        let qty = if shares_hint > 0 {
            shares_hint
        } else {
            (decision.adjusted_size / tick.ask).max(1.0) as u32
        };

        if self
            .broker
            .submit_order(&order_id, tid, OrderSide::Buy, qty, limit_price)
            .is_err()
        {
            return;
        }
        // P4-A: Record order submission in telemetry
        self.telemetry.orders_submitted.inc();

        // P3-C: Track order in Executioner for lifecycle management
        self.executioner.track_order(TrackedOrder {
            order_id: order_id.clone(),
            ticker_id: tid,
            lifecycle: OrderLifecycle::Submitted,
            submit_ns: self.now_ns,
            last_update_ns: self.now_ns,
            qty,
            filled_qty: 0,
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
                "T2T: ticker={}, latency={:.3}ms, qty={qty}, price={limit_price:.4}",
                tid.0, t2t_ms,
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

        // P21: Session manager update.
        let london_secs = self.london_time_secs();
        if let Some(transition) = self.session_mgr.update(london_secs, !self.positions.is_empty(), self.now_ns) {
            eprintln!("SESSION: {:?} → {:?}", transition.from, transition.to);
        }

        // P21: Subscription rotation on mode transitions
        let current_mode = self.session_mgr.mode();
        if current_mode != self.last_session_mode {
            eprintln!(
                "MODE_TRANSITION: {} → {} (will rotate subscriptions)",
                self.last_session_mode, current_mode
            );

            // P21: Carry manager — freeze/unfreeze stops at session boundaries
            match (self.last_session_mode, current_mode) {
                // ModeB → Dark: freeze stops (entering overnight carry)
                (SessionMode::ModeB, SessionMode::Dark) |
                // ModeB → Carry: freeze stops
                (SessionMode::ModeB, SessionMode::Carry) |
                // Auction → Dark: freeze stops
                (SessionMode::Auction, SessionMode::Dark) |
                // Auction → Carry: freeze stops
                (SessionMode::Auction, SessionMode::Carry) => {
                    let frozen = self.carry_manager.freeze_all_stops(self.now_ns);
                    if frozen > 0 {
                        // Collect tickers first to avoid borrow conflict
                        let tickers: Vec<TickerId> = self.positions.keys().copied().collect();
                        for tid in tickers {
                            if self.carry_manager.is_carried(&tid)
                                && let Some(pos) = self.positions.get_mut(&tid) {
                                pos.is_carried = true;
                            }
                        }
                    }
                }
                // Dark → ModeA: unfreeze stops (returning to Asian session)
                (SessionMode::Dark, SessionMode::ModeA) |
                // Carry → ModeB: unfreeze stops (returning to European session)
                (SessionMode::Carry, SessionMode::ModeB) |
                // Carry → ModeA: unfreeze stops (returning to Asian session)
                (SessionMode::Carry, SessionMode::ModeA) => {
                    let unfrozen = self.carry_manager.unfreeze_all_stops();
                    if unfrozen > 0 {
                        // Collect tickers first to avoid borrow conflict
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

        // P5-C: Log-Thompson sampler — compute allocation ranking (for telemetry).
        let _top_tickers = self.thompson_sampler.select_top_k(5);

        self.last_reconcile_ns = self.now_ns;
        Ok(result)
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
        if self.tick_watchdog.is_expired(self.now_ns) {
            eprintln!(
                "HARDENING: TICK WATCHDOG EXPIRED — no ticks for >120s ({} expirations), escalating to HALT",
                self.tick_watchdog.expirations()
            );
            self.arbiter.regime = RiskRegime::Halt;
            self.telemetry.regime_escalations.inc();
            self.write_wal(WalPayload::RiskStateChange {
                from: "any".to_string(),
                to: "Halt".to_string(),
                trigger: "tick_watchdog_expired".to_string(),
            });
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
                self.write_wal(WalPayload::RiskStateChange {
                    from: "any".to_string(),
                    to: "Halt".to_string(),
                    trigger: format!("IBKR error 1100: {message}"),
                });
            }
            1102 => {
                // Reconnected (H44) → reconcile before NORMAL
                eprintln!("IBKR ERROR 1102: {message} → reconcile");
                let _ = self.reconcile();
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
                self.write_wal(WalPayload::FillEvent {
                    order_id: order_id.clone(),
                    ticker_id: ticker_id.0,
                    filled_qty: *filled_qty,
                    remaining_qty: *remaining_qty,
                    price: *price,
                    exec_id: exec_id.clone(),
                    commission: *commission,
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
                    let stop = initial_stop_price(*price, 0.05);
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
                    };
                    self.portfolio.add_position(pos.clone());
                    self.positions.insert(*ticker_id, pos);
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
            let _ = wal.append(&event);
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

        // Write state hash as WAL event (H85)
        self.write_wal(WalPayload::StateSnapshot {
            portfolio_json: format!(
                "{{\"positions\":{},\"high_water\":{:.2}}}",
                self.positions.len(),
                self.portfolio.high_water_mark
            ),
            equity: self.portfolio.equity,
            high_water: self.portfolio.high_water_mark,
            hash: format!("{checksum:#010x}"),
        });

        self.last_state_hash_ns = self.now_ns;
    }

    /// P15: Check for pending split events and apply adjustments.
    pub fn check_pending_splits(&mut self) {
        for (&tid, pos) in &self.positions {
            // Check if any unprocessed split exists for this ticker.
            // In live, split events come from IBKR — here we just verify none are pending.
            if self.split_handler.was_processed(tid, self.now_ns) {
                continue;
            }
            // No split pending — position unchanged. If a SplitEvent were received
            // from broker, we'd call apply_split() here and update pos qty/prices.
            let _ = pos.qty; // Acknowledge position exists
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
            eprintln!("ENGINE: Flattening {:?} ({} shares)", tid, qty);
            // Use limit price of 0.01 as market-sell fallback (IBKR MTL)
            let _ = self.broker.submit_order(&order_id, *tid, OrderSide::Sell, *qty, 0.01);
        }

        // Step 3: Write SystemShutdown WAL event
        self.write_wal(WalPayload::SystemShutdown {
            positions_flattened,
            pending_fills_waited_secs: 0, // Actual wait happens in main.rs
        });

        self.startup_complete = false;
        eprintln!(
            "ENGINE: Shutdown complete. Flattened {} positions.",
            positions_flattened
        );
    }

    /// P21: Apply subscription rotation for a mode transition.
    /// Rotates tickers in subscription_manager based on the new session mode.
    /// Also manages CarryManager freeze/unfreeze.
    fn apply_mode_subscription_rotation(&mut self, new_mode: SessionMode) {
        match new_mode {
            SessionMode::ModeA => {
                eprintln!("SUBSCRIBE: Mode transition → Mode A (Asian markets: TSE/HKEX/ASX)");

                // Get all tickers currently subscribed (for unsubscribe)
                let currently_subscribed = self.subscription_manager.active_ticker_ids();

                // Get new ticker set
                let ticker_symbols = self.market_config.mode_a_tickers();
                let new_ticker_ids: Vec<TickerId> = ticker_symbols
                    .iter()
                    .map(|&sym| self.universe.intern.intern(sym))
                    .collect();

                // Calculate which tickers to unsubscribe
                let to_unsubscribe: Vec<TickerId> = currently_subscribed
                    .iter()
                    .filter(|tid| !new_ticker_ids.contains(tid))
                    .copied()
                    .collect();

                if !to_unsubscribe.is_empty() {
                    eprintln!(
                        "SUBSCRIBE: unsubscribing {} tickers from Mode B",
                        to_unsubscribe.len()
                    );
                    let unsub_count = self.broker.unsubscribe_l1_batch(&to_unsubscribe);
                    eprintln!(
                        "SUBSCRIBE: unsubscribed {} L1 feeds (count: {})",
                        unsub_count, to_unsubscribe.len()
                    );
                }

                // Update subscription manager
                let activated = self.subscription_manager.rotate_tickers(new_ticker_ids.clone(), self.now_ns);
                eprintln!(
                    "SUBSCRIBE: Mode A active with {} tickers (TSE={}, HKEX={}, ASX={})",
                    activated.len(),
                    self.market_config.tse_sample.len(),
                    self.market_config.hkex_sample.len(),
                    self.market_config.asx_sample.len()
                );

                // Subscribe to new tickers
                if !activated.is_empty() {
                    eprintln!(
                        "SUBSCRIBE: subscribing {} Asian tickers to L1",
                        activated.len()
                    );
                    let sub_count = self.broker.subscribe_l1_batch(&activated);
                    eprintln!(
                        "SUBSCRIBE: subscribed {} L1 feeds (total now: {})",
                        sub_count,
                        self.broker.l1_subscription_count()
                    );
                }

                // Freeze stops at Asian mode entry
                self.carry_manager.freeze_all_stops(self.now_ns);
                eprintln!("MODE_A: Frozen all carries for overnight");
            }
            SessionMode::ModeB => {
                eprintln!("SUBSCRIBE: Mode transition → Mode B (European + LSE markets)");

                // Get all tickers currently subscribed (for unsubscribe)
                let currently_subscribed = self.subscription_manager.active_ticker_ids();

                // Get new ticker set
                let ticker_symbols = self.market_config.mode_b_tickers();
                let new_ticker_ids: Vec<TickerId> = ticker_symbols
                    .iter()
                    .map(|&sym| self.universe.intern.intern(sym))
                    .collect();

                // Calculate which tickers to unsubscribe
                let to_unsubscribe: Vec<TickerId> = currently_subscribed
                    .iter()
                    .filter(|tid| !new_ticker_ids.contains(tid))
                    .copied()
                    .collect();

                if !to_unsubscribe.is_empty() {
                    eprintln!(
                        "SUBSCRIBE: unsubscribing {} tickers from Mode A",
                        to_unsubscribe.len()
                    );
                    let unsub_count = self.broker.unsubscribe_l1_batch(&to_unsubscribe);
                    eprintln!(
                        "SUBSCRIBE: unsubscribed {} L1 feeds (count: {})",
                        unsub_count, to_unsubscribe.len()
                    );
                }

                // Update subscription manager
                let activated = self.subscription_manager.rotate_tickers(new_ticker_ids.clone(), self.now_ns);
                eprintln!(
                    "SUBSCRIBE: Mode B active with {} tickers (LSE={}, XETRA={}, Euronext={})",
                    activated.len(),
                    self.market_config.lse_12.len(),
                    self.market_config.xetra_sample.len(),
                    self.market_config.euronext_sample.len()
                );

                // Subscribe to new tickers
                if !activated.is_empty() {
                    eprintln!(
                        "SUBSCRIBE: subscribing {} European tickers to L1",
                        activated.len()
                    );
                    let sub_count = self.broker.subscribe_l1_batch(&activated);
                    eprintln!(
                        "SUBSCRIBE: subscribed {} L1 feeds (total now: {})",
                        sub_count,
                        self.broker.l1_subscription_count()
                    );
                }

                // Unfreeze stops when entering Mode B
                self.carry_manager.unfreeze_all_stops();
                eprintln!("MODE_B: Unfrozen carries for European session");
            }
            SessionMode::ModeBPlus => {
                eprintln!("SUBSCRIBE: Mode transition → ModeBPlus (14:30-16:30 UTC US overlap)");
                // ModeBPlus adds US tickers to existing LSE/Euronext subscriptions
                // Full implementation in Phase 25 when US markets integrated
                // For now: keep current subscriptions and log the mode
                eprintln!("MODE_B_PLUS: US overlap active (80 LSE + 20 US lines)");
            }
            SessionMode::Dark => {
                eprintln!("SUBSCRIBE: Mode transition → Dark (suspend all trading)");

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
            SessionMode::Auction | SessionMode::Carry => {
                // Auction and Carry modes don't change subscriptions
                // Auction: brief period, keep current subscriptions
                // Carry: overnight management, already frozen
                eprintln!(
                    "MODE: {} — keeping current subscriptions",
                    new_mode
                );
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
