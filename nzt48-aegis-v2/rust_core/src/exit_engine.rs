//! Singular Canonical Exit Engine — ONE exit authority. Priority hierarchy resolves collisions.
//! Chandelier 5-rung profit ladder (Le Beau 1999). Shadow stops (H67). Stop ratchet (H68).

use crate::types::{ExitOrderType, ExitPriority, ExitReason, ExitSignal, PositionState};

/// Time-in-force for exit orders (H69).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TimeInForce {
    /// Normal entries and exits.
    Day,
    /// Emergency exits (HALT, failed exit retry).
    Ioc,
}

/// Exit strategy trait for hot-swappable exit math (H72).
pub trait ExitStrategy: Send {
    /// Compute the stop price for a position given current state.
    fn compute_stop(&self, pos: &PositionState, high: f64, atr: f64) -> f64;
    /// Determine which rung the position has reached.
    fn compute_rung(&self, pos: &PositionState, high: f64, atr: f64) -> u8;
    /// Override trailing ATR multiplier (Ouroboros learning layer). Default no-op.
    fn set_trail_atr(&mut self, _mult: f64) {}
    /// P4-C: Update adaptive multipliers (only InfiniteChandelier implements this).
    fn update_multipliers(&mut self, _vol: f64, _time_frac: f64, _momentum: f64,
                          _amihud: f64, _heat: f64, _is_reduce: bool) {}
}

/// Chandelier 5-rung profit ladder (Le Beau 1999).
/// Rung thresholds (ATR from entry): [0.5, 1.0, 1.5, 2.0, 3.0]
/// Stop offsets (ATR from entry):     [0.0, 0.25, 0.5, 1.0, trail 1.5 ATR from high]
pub struct ChandelierStrategy {
    /// ATR multiples to reach each rung (from entry price).
    pub rung_thresholds: [f64; 5],
    /// Stop offset from entry in ATR multiples (rungs 1-4). Rung 5 is trailing.
    pub rung_stops: [f64; 4],
    /// Trailing distance in ATR multiples for rung 5.
    pub rung5_trail_atr: f64,
}

impl Default for ChandelierStrategy {
    fn default() -> Self {
        Self {
            rung_thresholds: [0.5, 1.0, 1.5, 2.0, 3.0],
            rung_stops: [0.0, 0.25, 0.5, 1.0],
            rung5_trail_atr: 1.5,
        }
    }
}

impl ExitStrategy for ChandelierStrategy {
    fn compute_stop(&self, pos: &PositionState, high: f64, atr: f64) -> f64 {
        let rung = self.compute_rung(pos, high, atr);
        match rung {
            0 => pos.stop_price, // No rung reached, keep initial stop
            1..=4 => {
                let offset = self.rung_stops[(rung - 1) as usize];
                pos.avg_entry + offset * atr
            }
            _ => {
                // Rung 5: trail from highest_high
                high - self.rung5_trail_atr * atr
            }
        }
    }

    fn compute_rung(&self, pos: &PositionState, high: f64, atr: f64) -> u8 {
        if atr <= 0.0 {
            return 0;
        }
        let profit_atr = (high - pos.avg_entry) / atr;
        let mut rung = 0u8;
        for (i, &threshold) in self.rung_thresholds.iter().enumerate() {
            if profit_atr >= threshold {
                rung = (i + 1) as u8;
            }
        }
        rung
    }

    fn set_trail_atr(&mut self, mult: f64) {
        self.rung5_trail_atr = mult;
    }
}

/// Internal exit check result before collision resolution.
#[derive(Debug, Clone)]
struct ExitCheck {
    reason: ExitReason,
    priority: ExitPriority,
    order_type: ExitOrderType,
    limit_price: Option<f64>,
    tif: TimeInForce,
}

/// Configuration for the exit engine.
#[derive(Clone, Debug)]
pub struct ExitConfig {
    /// EOD flatten time in seconds from midnight London (16:25 = 59100).
    pub eod_flatten_secs: u32,
    /// Price spike filter threshold (10% = 0.10).
    pub price_spike_pct: f64,
    /// Minimum expected value after commission to keep a trade open.
    pub min_ev_after_commission: f64,
    /// SC-06: Dust threshold in GBP. Remainder below this → market sell.
    pub dust_threshold_gbp: f64,
    /// P4-C: Use InfiniteChandelier (8 adaptive multipliers) instead of basic ChandelierStrategy.
    pub use_infinite_chandelier: bool,
}

impl Default for ExitConfig {
    fn default() -> Self {
        Self {
            eod_flatten_secs: 59100, // 16:25 London
            price_spike_pct: 0.10,
            min_ev_after_commission: 0.0,
            dust_threshold_gbp: 500.0,
            use_infinite_chandelier: false,
        }
    }
}

/// The singular canonical exit engine. ONE instance. No duplicates.
/// Shadow stops: all stops computed internally, NOT native IBKR trailing stops (H67).
pub struct ExitEngine {
    pub config: ExitConfig,
    strategy: Box<dyn ExitStrategy>,
}

impl ExitEngine {
    pub fn new(config: ExitConfig, strategy: Box<dyn ExitStrategy>) -> Self {
        Self { config, strategy }
    }

    pub fn with_default_chandelier() -> Self {
        Self::new(
            ExitConfig::default(),
            Box::new(ChandelierStrategy::default()),
        )
    }

    /// P4-C: Construct ExitEngine with InfiniteChandelier (8 adaptive multipliers).
    pub fn with_infinite_chandelier() -> Self {
        let config = ExitConfig {
            use_infinite_chandelier: true,
            ..ExitConfig::default()
        };
        Self::new(config, Box::new(InfiniteChandelier::new()))
    }

    /// Mutable access to the exit strategy for Ouroboros parameter injection.
    pub fn strategy_mut(&mut self) -> &mut dyn ExitStrategy {
        &mut *self.strategy
    }

    /// P4-C: Check if using InfiniteChandelier.
    pub fn is_infinite_chandelier(&self) -> bool {
        self.config.use_infinite_chandelier
    }

    /// Evaluate ALL exit conditions for a position on the current tick.
    /// Returns the highest-priority exit that fires, or None.
    /// Priority: HALT > HardStop > Chandelier > EOD > Signal.
    /// Same-tick collision: highest priority wins, lower suppressed.
    #[allow(clippy::too_many_arguments)]
    pub fn evaluate(
        &self,
        position: &PositionState,
        current_price: f64,
        atr: f64,
        time_secs: u32,
        is_halt_flatten: bool,
        signal_reversal: bool,
        is_carried: bool,
    ) -> Option<ExitResult> {
        let mut checks: Vec<ExitCheck> = Vec::new();

        // Priority 5: HALT/FLATTEN override — market sell immediately
        if is_halt_flatten {
            checks.push(ExitCheck {
                reason: ExitReason::HaltFlatten,
                priority: ExitPriority::HaltFlatten,
                order_type: ExitOrderType::MarketToLimit, // MTL for emergency (H117)
                limit_price: None,
                tif: TimeInForce::Ioc, // Emergency = IOC (H69)
            });
        }

        // Priority 4: Hard stop-loss
        if current_price <= position.stop_price {
            checks.push(ExitCheck {
                reason: ExitReason::HardStopLoss,
                priority: ExitPriority::HardStopLoss,
                order_type: ExitOrderType::LimitAtStop,
                limit_price: Some(position.stop_price),
                tif: TimeInForce::Day, // Normal exit = DAY (H69)
            });
        }

        // Priority 3: Chandelier trailing stop
        // P21: Skip Chandelier evaluation if position is in carry state (stops frozen)
        if !is_carried {
            let chandelier_stop = self
                .strategy
                .compute_stop(position, position.highest_high, atr);
            if current_price <= chandelier_stop && chandelier_stop > position.stop_price {
                checks.push(ExitCheck {
                    reason: ExitReason::ChandelierTrailing,
                    priority: ExitPriority::ChandelierStop,
                    order_type: ExitOrderType::LimitAtStop,
                    limit_price: Some(chandelier_stop),
                    tif: TimeInForce::Day,
                });
            }
        }

        // Priority 2: EOD flatten
        if time_secs >= self.config.eod_flatten_secs {
            checks.push(ExitCheck {
                reason: ExitReason::EodFlatten,
                priority: ExitPriority::EodFlatten,
                order_type: ExitOrderType::MarketSell,
                limit_price: None,
                tif: TimeInForce::Day,
            });
        }

        // Priority 1: Signal reversal
        if signal_reversal {
            checks.push(ExitCheck {
                reason: ExitReason::SignalReversal,
                priority: ExitPriority::SignalReversal,
                order_type: ExitOrderType::MarketSell,
                limit_price: None,
                tif: TimeInForce::Day,
            });
        }

        if checks.is_empty() {
            return None;
        }

        // Sort by priority descending — highest priority wins
        checks.sort_by(|a, b| b.priority.cmp(&a.priority));
        let winner = &checks[0];

        // HALT override: if HALT fires, everything becomes MarketToLimit + IOC
        let (order_type, tif, limit_price) = if is_halt_flatten {
            (ExitOrderType::MarketToLimit, TimeInForce::Ioc, None)
        } else {
            (winner.order_type, winner.tif, winner.limit_price)
        };

        Some(ExitResult {
            signal: ExitSignal {
                ticker_id: position.ticker_id,
                reason: winner.reason,
                priority: winner.priority,
                order_type,
                position_order_id: position.origin_order_id.clone(),
                limit_price,
            },
            tif,
            suppressed_count: checks.len() - 1,
        })
    }

    /// Update position tracking on each tick. Call BEFORE evaluate.
    /// Updates highest_high (H70), rung, and stop price (H68: ratchet UP only).
    pub fn update_tracking(&self, position: &mut PositionState, current_price: f64, atr: f64) {
        // Update highest_high
        if current_price > position.highest_high {
            position.highest_high = current_price;
        }
        // Compute new rung (can only increase)
        let new_rung = self
            .strategy
            .compute_rung(position, position.highest_high, atr);
        if new_rung > position.trailing_rung {
            position.trailing_rung = new_rung;
        }
        // Compute new stop (H68: can NEVER decrease)
        let new_stop = self
            .strategy
            .compute_stop(position, position.highest_high, atr);
        if new_stop > position.stop_price {
            position.stop_price = new_stop;
        }
    }

    /// Price spike filter (H71): detect if a price drop is likely a spike artifact.
    /// Returns true if the drop looks like a spike (should NOT trigger stop).
    pub fn is_price_spike(&self, prev_price: f64, current_price: f64, bid: f64, ask: f64) -> bool {
        if prev_price <= 0.0 {
            return false;
        }
        let drop_pct = (prev_price - current_price) / prev_price;
        if drop_pct < self.config.price_spike_pct {
            return false;
        }
        // Verify bid/ask midpoint is still reasonable
        let midpoint = (bid + ask) / 2.0;
        let mid_drop = (prev_price - midpoint) / prev_price;
        // If midpoint didn't drop nearly as much, it's a spike
        mid_drop < self.config.price_spike_pct * 0.5
    }

    /// SC-06: Check if position remainder is dust (below £500 threshold).
    /// Returns true if the position's current market value is below the dust threshold.
    pub fn is_dust(&self, current_price: f64, qty: u32) -> bool {
        let value_gbp = current_price * qty as f64;
        value_gbp < self.config.dust_threshold_gbp
    }

    /// SC-06: Generate a dust guard exit signal for a position below the threshold.
    /// Call this after partial fills to clean up small remainders.
    pub fn dust_guard_exit(&self, position: &PositionState, current_price: f64) -> Option<ExitResult> {
        if !self.is_dust(current_price, position.qty) {
            return None;
        }

        Some(ExitResult {
            signal: ExitSignal {
                ticker_id: position.ticker_id,
                reason: ExitReason::DustGuard,
                priority: ExitPriority::DustGuard,
                order_type: ExitOrderType::MarketSell,
                position_order_id: position.origin_order_id.clone(),
                limit_price: None,
            },
            tif: TimeInForce::Day,
            suppressed_count: 0,
        })
    }

    /// Commission check (H73): compute expected value after commission.
    /// Returns EV. Caller should reject/close if EV < min_ev_after_commission.
    pub fn ev_after_commission(
        entry_price: f64,
        current_price: f64,
        qty: u32,
        commission: f64,
    ) -> f64 {
        let gross_pnl = (current_price - entry_price) * qty as f64;
        gross_pnl - commission
    }
}

/// Result of exit evaluation including TIF and suppressed exit count.
#[derive(Debug, Clone)]
pub struct ExitResult {
    pub signal: ExitSignal,
    pub tif: TimeInForce,
    pub suppressed_count: usize,
}

/// Helper: compute initial stop price for a new position.
pub fn initial_stop_price(entry_price: f64, stop_pct: f64) -> f64 {
    entry_price * (1.0 - stop_pct)
}

/// Helper: determine TIF for entry orders (H69).
pub fn entry_tif() -> TimeInForce {
    TimeInForce::Day
}

/// Helper: determine TIF for emergency exits (H69).
pub fn emergency_tif() -> TimeInForce {
    TimeInForce::Ioc
}

// ────────────────────────────────────────────
// Phase 14: Infinite Chandelier — 8 adaptive multipliers + mega-runner
// ────────────────────────────────────────────

/// Adaptive ATR multiplier factors.
/// The effective trailing distance = base_trail × product(all_active_multipliers).
#[derive(Clone, Debug)]
pub struct AdaptiveMultipliers {
    /// Volatility regime (high vol → wider stops). [0.8, 1.5]
    pub volatility: f64,
    /// Correlation with market (high correlation → tighter). [0.9, 1.1]
    pub correlation: f64,
    /// Time-of-day (tighter near close). [0.8, 1.0]
    pub time_decay: f64,
    /// Momentum strength (strong momentum → wider to ride). [1.0, 1.3]
    pub momentum: f64,
    /// Liquidity (illiquid → wider stops to avoid whipsaw). [1.0, 1.4]
    pub liquidity: f64,
    /// Portfolio heat (high heat → tighter to protect). [0.7, 1.0]
    pub heat: f64,
    /// Regime (Reduce → tighter, Normal → neutral). [0.6, 1.0]
    pub regime: f64,
    /// Mega-runner bonus (position > 3 ATR profit → wider trail). [1.0, 2.0]
    pub mega_runner: f64,
}

impl Default for AdaptiveMultipliers {
    fn default() -> Self {
        Self {
            volatility: 1.0,
            correlation: 1.0,
            time_decay: 1.0,
            momentum: 1.0,
            liquidity: 1.0,
            heat: 1.0,
            regime: 1.0,
            mega_runner: 1.0,
        }
    }
}

impl AdaptiveMultipliers {
    /// Compute the combined multiplier.
    pub fn combined(&self) -> f64 {
        self.volatility
            * self.correlation
            * self.time_decay
            * self.momentum
            * self.liquidity
            * self.heat
            * self.regime
            * self.mega_runner
    }

    /// Update volatility multiplier from realized vol.
    /// Low vol (< 20% ann) → 0.8, High vol (> 50% ann) → 1.5
    pub fn update_volatility(&mut self, realized_vol_ann: f64) {
        self.volatility = (0.8 + (realized_vol_ann - 0.20) * (0.7 / 0.30)).clamp(0.8, 1.5);
    }

    /// Update time decay: linear tightening from 1.0 at open to 0.8 at close.
    pub fn update_time_decay(&mut self, time_fraction: f64) {
        self.time_decay = (1.0 - 0.2 * time_fraction).clamp(0.8, 1.0);
    }

    /// Update momentum multiplier from price momentum percentage.
    pub fn update_momentum(&mut self, momentum_pct: f64) {
        self.momentum = (1.0 + momentum_pct.abs() * 10.0).clamp(1.0, 1.3);
    }

    /// Update liquidity from Amihud illiquidity ratio.
    pub fn update_liquidity(&mut self, amihud: f64) {
        self.liquidity = (1.0 + amihud * 40.0).clamp(1.0, 1.4);
    }

    /// Update heat multiplier from portfolio heat percentage.
    pub fn update_heat(&mut self, heat_pct: f64) {
        // High heat (>8%) → tighter (0.7), low heat (<2%) → neutral (1.0)
        self.heat = (1.0 - (heat_pct - 2.0).max(0.0) * (0.3 / 6.0)).clamp(0.7, 1.0);
    }

    /// Update regime multiplier.
    pub fn update_regime(&mut self, is_reduce: bool) {
        self.regime = if is_reduce { 0.6 } else { 1.0 };
    }

    /// Update mega-runner bonus based on profit in ATR multiples.
    /// > 3 ATR profit → gradually widen to let winners run.
    pub fn update_mega_runner(&mut self, profit_atr: f64) {
        if profit_atr > 3.0 {
            self.mega_runner = (1.0 + (profit_atr - 3.0) * 0.2).clamp(1.0, 2.0);
        } else {
            self.mega_runner = 1.0;
        }
    }
}

/// Infinite Chandelier: Chandelier with 8 adaptive multipliers.
/// The effective trail distance = base_rung5_trail × combined_multiplier.
pub struct InfiniteChandelier {
    pub base: ChandelierStrategy,
    pub multipliers: AdaptiveMultipliers,
}

impl InfiniteChandelier {
    pub fn new() -> Self {
        Self {
            base: ChandelierStrategy::default(),
            multipliers: AdaptiveMultipliers::default(),
        }
    }

    /// Compute adaptive trailing stop for rung 5 (mega-runner path).
    pub fn adaptive_trail(&self, high: f64, atr: f64) -> f64 {
        let effective_trail = self.base.rung5_trail_atr * self.multipliers.combined();
        high - effective_trail * atr
    }
}

impl Default for InfiniteChandelier {
    fn default() -> Self {
        Self::new()
    }
}

impl ExitStrategy for InfiniteChandelier {
    fn compute_stop(&self, pos: &PositionState, high: f64, atr: f64) -> f64 {
        let rung = self.compute_rung(pos, high, atr);
        match rung {
            0 => pos.stop_price,
            1..=4 => {
                let offset = self.base.rung_stops[(rung - 1) as usize];
                pos.avg_entry + offset * atr
            }
            _ => {
                // Rung 5+: adaptive trailing with multipliers
                self.adaptive_trail(high, atr)
            }
        }
    }

    fn compute_rung(&self, pos: &PositionState, high: f64, atr: f64) -> u8 {
        self.base.compute_rung(pos, high, atr)
    }

    fn set_trail_atr(&mut self, mult: f64) {
        self.base.rung5_trail_atr = mult;
    }

    fn update_multipliers(&mut self, vol: f64, time_frac: f64, momentum: f64,
                          amihud: f64, heat: f64, is_reduce: bool) {
        self.multipliers.update_volatility(vol);
        self.multipliers.update_time_decay(time_frac);
        self.multipliers.update_momentum(momentum);
        self.multipliers.update_liquidity(amihud);
        self.multipliers.update_heat(heat);
        self.multipliers.update_regime(is_reduce);
    }
}

// ────────────────────────────────────────────
// Phase 14: Executioner V2 — Order lifecycle management
// ────────────────────────────────────────────

/// Order lifecycle states for the Executioner.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OrderLifecycle {
    /// Order created, not yet submitted.
    Pending,
    /// Submitted to broker, awaiting ack.
    Submitted,
    /// Acknowledged by broker.
    Acknowledged,
    /// Partially filled.
    PartialFill,
    /// Fully filled.
    Filled,
    /// Cancel requested.
    CancelPending,
    /// Cancelled.
    Cancelled,
    /// Rejected by broker.
    Rejected,
    /// Replace (modify) pending.
    ReplacePending,
}

/// Tracked order in the Executioner.
#[derive(Clone, Debug)]
pub struct TrackedOrder {
    pub order_id: String,
    pub ticker_id: crate::types::TickerId,
    pub lifecycle: OrderLifecycle,
    pub qty: u32,
    pub filled_qty: u32,
    pub limit_price: f64,
    pub submit_ns: u64,
    pub last_update_ns: u64,
    /// Number of retry attempts (for failed submissions).
    pub retries: u32,
    /// Whether this is an entry or exit order.
    pub is_exit: bool,
}

/// Executioner V2: manages order lifecycle and retry logic.
pub struct Executioner {
    /// Active tracked orders by order_id.
    orders: std::collections::HashMap<String, TrackedOrder>,
    /// Maximum retries for failed order submission.
    pub max_retries: u32,
    /// Timeout for order acknowledgement (nanoseconds). Default: 5s.
    pub ack_timeout_ns: u64,
    /// Timeout for fill after submission (nanoseconds). Default: 60s.
    pub fill_timeout_ns: u64,
}

impl Executioner {
    pub fn new() -> Self {
        Self {
            orders: std::collections::HashMap::new(),
            max_retries: 3,
            ack_timeout_ns: 5_000_000_000,
            fill_timeout_ns: 60_000_000_000,
        }
    }

    /// Track a new order.
    pub fn track_order(&mut self, order: TrackedOrder) {
        self.orders.insert(order.order_id.clone(), order);
    }

    /// Update order lifecycle state.
    pub fn update_lifecycle(&mut self, order_id: &str, state: OrderLifecycle, now_ns: u64) {
        if let Some(order) = self.orders.get_mut(order_id) {
            order.lifecycle = state;
            order.last_update_ns = now_ns;
        }
    }

    /// Record a partial or full fill.
    pub fn record_fill(&mut self, order_id: &str, filled_qty: u32, now_ns: u64) {
        if let Some(order) = self.orders.get_mut(order_id) {
            order.filled_qty += filled_qty;
            order.last_update_ns = now_ns;
            if order.filled_qty >= order.qty {
                order.lifecycle = OrderLifecycle::Filled;
            } else {
                order.lifecycle = OrderLifecycle::PartialFill;
            }
        }
    }

    /// Get all orders that have timed out waiting for ack.
    pub fn stale_unacked(&self, now_ns: u64) -> Vec<&TrackedOrder> {
        self.orders
            .values()
            .filter(|o| {
                o.lifecycle == OrderLifecycle::Submitted
                    && now_ns > o.submit_ns + self.ack_timeout_ns
            })
            .collect()
    }

    /// Get all orders that have timed out waiting for fill.
    pub fn stale_unfilled(&self, now_ns: u64) -> Vec<&TrackedOrder> {
        self.orders
            .values()
            .filter(|o| {
                matches!(
                    o.lifecycle,
                    OrderLifecycle::Acknowledged | OrderLifecycle::PartialFill
                ) && now_ns > o.last_update_ns + self.fill_timeout_ns
            })
            .collect()
    }

    /// Get an order by ID.
    pub fn get(&self, order_id: &str) -> Option<&TrackedOrder> {
        self.orders.get(order_id)
    }

    /// Remove completed/cancelled orders.
    pub fn prune_completed(&mut self) {
        self.orders.retain(|_, o| {
            !matches!(
                o.lifecycle,
                OrderLifecycle::Filled | OrderLifecycle::Cancelled | OrderLifecycle::Rejected
            )
        });
    }

    /// Active order count.
    pub fn active_count(&self) -> usize {
        self.orders
            .values()
            .filter(|o| {
                !matches!(
                    o.lifecycle,
                    OrderLifecycle::Filled | OrderLifecycle::Cancelled | OrderLifecycle::Rejected
                )
            })
            .count()
    }

    /// Total tracked orders.
    pub fn total_count(&self) -> usize {
        self.orders.len()
    }
}

impl Default for Executioner {
    fn default() -> Self {
        Self::new()
    }
}
