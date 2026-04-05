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
    /// Override initial stop ATR multiplier (Ouroboros learning layer). Default no-op.
    fn set_initial_stop_atr(&mut self, _mult: f64) {}
    /// P4-C: Update adaptive multipliers (only InfiniteChandelier implements this).
    fn update_multipliers(&mut self, _vol: f64, _time_frac: f64, _momentum: f64,
                          _amihud: f64, _heat: f64, _is_reduce: bool) {}
    /// P1-2.6: Update mega-runner bonus based on profit in ATR multiples.
    fn update_mega_runner(&mut self, _profit_atr: f64) {}
    /// Sprint G: Check volume exhaustion. If current RVOL exceeds the exhaustion threshold,
    /// returns Some(tight_atr) — the tightened ATR multiplier for the stop.
    /// The engine calls this before compute_stop and uses the tighter value if triggered.
    fn check_exhaustion(&self, _current_rvol: f64) -> Option<f64> { None }
}

/// Chandelier trailing-stop-only strategy — NO partial sells, FULL position rides.
///
/// The stop tightens as profit grows. The ONLY exit trigger is when price hits the stop.
/// All sells are 100% of position.
///
/// Rung thresholds (percentage gain from entry):
///   Rung 1: Entry          → Stop = entry - 2x ATR (widened from 1x to reduce noise exits)
///   Rung 2: +2% from entry → Stop = breakeven INCLUDING round-trip fees
///   Rung 3: +4% from entry → Trail 1.0x ATR below peak (NO partial sell)
///   Rung 4: +6% from entry → Trail 0.75x ATR below peak (NO partial sell)
///   Rung 5: +8% from entry → Trail 0.5x ATR below peak (NO partial sell)
///   Beyond: every +2% more → keep trailing at 0.5x ATR below peak
#[derive(Clone)]
pub struct ChandelierStrategy {
    /// Percentage gain thresholds to reach each rung [0.0, 0.02, 0.04, 0.06, 0.08].
    pub rung_pct_thresholds: [f64; 5],
    /// ATR trailing multiplier for Rung 3.
    pub rung3_trail_atr: f64,
    /// ATR trailing multiplier for Rung 4.
    pub rung4_trail_atr: f64,
    /// ATR trailing multiplier for Rung 5+ (tightest trail, lets winners run).
    pub rung5_trail_atr: f64,
    /// Estimated round-trip fee as a fraction of entry price.
    /// Covers: entry commission + exit commission + spread cost.
    /// Default 0.002 (0.2%) — conservative for LSE leveraged ETPs.
    pub round_trip_fee_pct: f64,
    /// Sprint 6: Initial stop ATR multiplier (rung 1). Default 1.5.
    pub initial_stop_atr_mult: f64,
    /// Sprint 6: ATR floor as fraction of entry price. Default 0.005 (0.5%).
    pub atr_floor_pct: f64,
    /// Sprint G: Volume exhaustion — tighten stop when RVOL signals climactic reversal.
    pub exhaustion_enabled: bool,
    /// Sprint G: RVOL threshold for exhaustion (e.g., 10.0 = 10x normal volume).
    pub exhaustion_rvol_mult: f64,
    /// Sprint G: Tight ATR multiplier used during exhaustion (e.g., 0.5).
    pub exhaustion_tight_atr: f64,
}

impl Default for ChandelierStrategy {
    fn default() -> Self {
        Self {
            // AUDIT-RESTRUCTURED for compounding optimality (2026-03-18).
            // Quant audit: "Rung spacing too wide. A system that wins 60% at +1.2%
            // compounds faster than one that wins 50% at +2.0%."
            // Old: [0.0, 0.02, 0.04, 0.06, 0.08] — Rung 2 at +2% was too far,
            // most trades never reached breakeven lock.
            // New: tighter rungs prioritize Rung 3 attainment (the "compounding unit").
            rung_pct_thresholds: [0.0, 0.008, 0.015, 0.025, 0.040],
            // Rung 1: entry         → Stop = entry - 1.5x ATR (tightened from 2x)
            // Rung 2: +0.8%        → Stop = breakeven + fees (lock in near-zero quickly)
            // Rung 3: +1.5%        → Trail 1.0x ATR below peak (the compounding unit)
            // Rung 4: +2.5%        → Trail 0.75x ATR below peak
            // Rung 5: +4.0%        → Trail 0.5x ATR below peak (tail capture)
            rung3_trail_atr: 1.0,    // Rung 3: 1.0x ATR below peak
            rung4_trail_atr: 0.75,   // Rung 4: 0.75x ATR below peak
            rung5_trail_atr: 0.5,    // Rung 5+: 0.5x ATR below peak (tightest)
            round_trip_fee_pct: 0.003, // 0.3% round-trip (conservative for LSE 3x ETPs)
            initial_stop_atr_mult: 2.0,
            atr_floor_pct: 0.005,
            exhaustion_enabled: true,
            exhaustion_rvol_mult: 10.0,
            exhaustion_tight_atr: 0.5,
        }
    }
}

impl ChandelierStrategy {
    /// Sprint 6: Construct from config values.
    pub fn from_config(
        rung_pct: &[f64],
        initial_stop_atr_mult: f64,
        rung3_trail_atr: f64,
        rung4_trail_atr: f64,
        rung5_trail_atr: f64,
        round_trip_fee_pct: f64,
        atr_floor_pct: f64,
    ) -> Self {
        let mut thresholds = [0.0; 5];
        for (i, val) in rung_pct.iter().take(5).enumerate() {
            thresholds[i] = *val;
        }
        Self {
            rung_pct_thresholds: thresholds,
            rung3_trail_atr,
            rung4_trail_atr,
            rung5_trail_atr,
            round_trip_fee_pct,
            initial_stop_atr_mult,
            atr_floor_pct,
            // Sprint G: Defaults — caller should override via set_exhaustion_config
            exhaustion_enabled: true,
            exhaustion_rvol_mult: 10.0,
            exhaustion_tight_atr: 0.5,
        }
    }

    /// Sprint G: Configure volume exhaustion parameters from config.
    pub fn set_exhaustion_config(&mut self, enabled: bool, rvol_mult: f64, tight_atr: f64) {
        self.exhaustion_enabled = enabled;
        self.exhaustion_rvol_mult = rvol_mult;
        self.exhaustion_tight_atr = tight_atr;
    }

    /// Book 39: Create a per-signal override of Chandelier parameters.
    /// Returns a modified copy with signal-specific stop widths.
    pub fn with_signal_hints(
        &self,
        suggested_initial_stop_atr_mult: Option<f64>,
        suggested_rung3_atr: Option<f64>,
        exit_trail_bias: Option<&str>,
    ) -> Self {
        let mut s = self.clone();
        // Override initial stop ATR multiplier (leverage-adjusted from bridge.py)
        if let Some(mult) = suggested_initial_stop_atr_mult {
            s.initial_stop_atr_mult = mult.clamp(0.5, 5.0);
        }
        // Override rung 3 trail ATR (regime-adaptive from bridge.py)
        if let Some(r3) = suggested_rung3_atr {
            s.rung3_trail_atr = r3.clamp(0.3, 3.0);
        }
        // AUDIT-FIX HIGH#3: trail_bias was applied TWICE — once here and once in
        // compute_stop(). Removed from here. Bias is now applied ONLY in compute_stop()
        // per-tick, using pos.exit_trail_bias (stored on the PositionState).
        let _ = exit_trail_bias; // suppress unused warning — bias applied in compute_stop()
        s
    }
}

impl ExitStrategy for ChandelierStrategy {
    fn compute_stop(&self, pos: &PositionState, high: f64, atr: f64) -> f64 {
        // ATR floor: minimum atr_floor_pct of entry price to prevent zombie positions when ATR=0 on cold start
        let atr = atr.max(pos.avg_entry * self.atr_floor_pct);
        let rung = self.compute_rung(pos, high, atr);
        // Book 39: Apply exit_trail_bias from BrainSignal — "wide" widens stops 1.3x,
        // "tight" tightens 0.7x. Trending regimes should let winners run (wide),
        // mean-reverting regimes should capture quickly (tight).
        let bias_mult = match pos.exit_trail_bias.as_deref() {
            Some("wide") => 1.3,
            Some("tight") => 0.7,
            _ => 1.0,
        };
        // Also apply per-signal rung3 override if present
        let r3 = pos.suggested_rung3_atr.unwrap_or(self.rung3_trail_atr) * bias_mult;
        let r4 = self.rung4_trail_atr * bias_mult;
        let r5 = self.rung5_trail_atr * bias_mult;
        match rung {
            // Rung 0: not yet at entry rung — keep initial stop
            0 => pos.stop_price,
            // Rung 1: entry. Stop = entry - Nx ATR (configurable, default 1.5)
            1 => pos.avg_entry - self.initial_stop_atr_mult * atr,
            // Rung 2: +2% gain. Stop = breakeven INCLUDING round-trip fees.
            // Book 177: Don't advance to breakeven unless unrealized P&L > min_profit_target_pct.
            // On wide-spread instruments, premature breakeven stops get hit by spread noise.
            2 => {
                let fee_amount = pos.avg_entry * self.round_trip_fee_pct;
                // Check min_profit_target: if set and gain hasn't reached it, stay at Rung 1
                if let Some(min_pct) = pos.min_profit_target_pct {
                    let gain_pct = (high - pos.avg_entry) / pos.avg_entry;
                    if gain_pct < min_pct / 100.0 {
                        // Not enough profit to justify breakeven — stay at Rung 1 stop
                        return pos.avg_entry - self.initial_stop_atr_mult * atr;
                    }
                }
                pos.avg_entry + fee_amount
            }
            // Rung 3: Trail r3 ATR below peak (bias-adjusted). NO partial sell.
            3 => high - r3 * atr,
            // Rung 4: Trail r4 ATR below peak (bias-adjusted). NO partial sell.
            4 => high - r4 * atr,
            // Rung 5+: Trail r5 ATR below peak (bias-adjusted). NO partial sell.
            _ => high - r5 * atr,
        }
    }

    fn compute_rung(&self, pos: &PositionState, high: f64, _atr: f64) -> u8 {
        if pos.avg_entry <= 0.0 {
            return 0;
        }
        // Percentage gain from entry (based on highest high, not current price)
        let gain_pct = (high - pos.avg_entry) / pos.avg_entry;
        let mut rung = 0u8;
        for (i, &threshold) in self.rung_pct_thresholds.iter().enumerate() {
            if gain_pct >= threshold {
                rung = (i + 1) as u8;
            }
        }
        rung
    }

    fn set_trail_atr(&mut self, mult: f64) {
        self.rung5_trail_atr = mult;
    }

    fn set_initial_stop_atr(&mut self, mult: f64) {
        self.initial_stop_atr_mult = mult;
    }

    fn check_exhaustion(&self, current_rvol: f64) -> Option<f64> {
        if self.exhaustion_enabled && current_rvol >= self.exhaustion_rvol_mult {
            Some(self.exhaustion_tight_atr)
        } else {
            None
        }
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
    /// Default EOD flatten time in seconds from midnight UTC.
    /// Set to the EARLIEST exchange close (TSE/KRX = 06:00 UTC = 21600) to be safe:
    /// positions get flattened too early rather than too late.
    pub eod_flatten_secs: u32,
    /// Per-exchange EOD flatten times in seconds from midnight UTC.
    /// Keys are exchange names (e.g. "LSE", "TSE", "SMART").
    /// If a position's exchange is found here, this overrides eod_flatten_secs.
    /// Default map:
    ///   LSE/LSEETF: 59100 (16:25 London)
    ///   XETRA/FWB:  57600 (16:00 London / 17:00 CET)
    ///   TSE:        21600 (06:00 UTC)
    ///   HKEX/SEHK:  28800 (08:00 UTC)
    ///   KRX/KSE:    21600 (06:00 UTC)
    ///   SGX:        28800 (08:00 UTC)
    ///   SMART/NYSE/NASDAQ: 72900 (20:15 UTC / 16:15 ET)
    pub eod_flatten_per_exchange: std::collections::HashMap<String, u32>,
    /// Price spike filter threshold (10% = 0.10).
    pub price_spike_pct: f64,
    /// Minimum expected value after commission to keep a trade open.
    pub min_ev_after_commission: f64,
    /// SC-06: Dust threshold in GBP. Remainder below this → market sell.
    pub dust_threshold_gbp: f64,
    /// P4-C: Use InfiniteChandelier (8 adaptive multipliers) instead of basic ChandelierStrategy.
    pub use_infinite_chandelier: bool,
    /// S3: Time-stop — if position hasn't reached rung 2 within this many minutes,
    /// tighten the trailing stop aggressively to force an exit on sideways positions.
    pub time_stop_enabled: bool,
    /// S3: Maximum minutes to reach rung 2 before aggressive trail kicks in.
    pub time_stop_max_minutes_to_rung2: u32,
    /// S3: Aggressive ATR multiplier applied when time-stop triggers (e.g., 0.3).
    pub time_stop_aggressive_trail_atr: f64,
}

impl ExitConfig {
    /// Build the default per-exchange EOD flatten map.
    pub fn default_eod_map() -> std::collections::HashMap<String, u32> {
        let mut m = std::collections::HashMap::new();
        // London Stock Exchange: 16:25 London ≈ 59100s UTC (ignoring BST for safety)
        m.insert("LSE".into(), 59100);
        m.insert("LSEETF".into(), 59100);
        // Frankfurt / XETRA: 17:00 CET = 16:00 London ≈ 57600s UTC
        m.insert("XETRA".into(), 57600);
        m.insert("FWB".into(), 57600);
        // Tokyo Stock Exchange: 06:00 UTC = 21600s
        m.insert("TSE".into(), 21600);
        // Hong Kong: 08:00 UTC = 28800s
        m.insert("HKEX".into(), 28800);
        m.insert("SEHK".into(), 28800);
        // Korea: 06:00 UTC = 21600s
        m.insert("KRX".into(), 21600);
        m.insert("KSE".into(), 21600);
        // Singapore: 08:00 UTC = 28800s
        m.insert("SGX".into(), 28800);
        // US exchanges: 16:15 ET ≈ 20:15 UTC = 72900s
        m.insert("SMART".into(), 72900);
        m.insert("NYSE".into(), 72900);
        m.insert("NASDAQ".into(), 72900);
        m.insert("ARCA".into(), 72900);
        m
    }

    /// Look up EOD flatten time for a given exchange. Falls back to the global default.
    pub fn eod_flatten_for_exchange(&self, exchange: &str) -> u32 {
        self.eod_flatten_per_exchange
            .get(exchange)
            .copied()
            .unwrap_or(self.eod_flatten_secs)
    }
}

impl Default for ExitConfig {
    fn default() -> Self {
        Self {
            // Default to EARLIEST exchange close (TSE/KRX 06:00 UTC) — safe fallback.
            // Positions on unknown exchanges get flattened early rather than held too late.
            eod_flatten_secs: 21600, // 06:00 UTC (TSE/KRX close)
            eod_flatten_per_exchange: Self::default_eod_map(),
            price_spike_pct: 0.10,
            min_ev_after_commission: 0.0,
            dust_threshold_gbp: 500.0,
            use_infinite_chandelier: false,
            time_stop_enabled: true,
            time_stop_max_minutes_to_rung2: 45,
            time_stop_aggressive_trail_atr: 0.3,
        }
    }
}

/// The singular canonical exit engine. ONE instance. No duplicates.
/// Shadow stops: all stops computed internally, NOT native IBKR trailing stops (H67).
pub struct ExitEngine {
    pub config: ExitConfig,
    strategy: Box<dyn ExitStrategy>,
    /// Book 39: Concrete default ChandelierStrategy for applying per-position signal hints.
    /// When the strategy is InfiniteChandelier, this holds a fallback ChandelierStrategy
    /// so with_signal_hints() can still work for positions with exit hints.
    default_chandelier: ChandelierStrategy,
    /// Book 39: Per-strategy Chandelier overrides. Key = entry_type (e.g., "TypeF", "S2").
    /// When a position's entry_type matches, that strategy's params are used instead of global.
    per_strategy_overrides: std::collections::HashMap<String, ChandelierStrategy>,
}

impl ExitEngine {
    pub fn new(config: ExitConfig, strategy: Box<dyn ExitStrategy>) -> Self {
        Self { config, strategy, default_chandelier: ChandelierStrategy::default(), per_strategy_overrides: std::collections::HashMap::new() }
    }

    /// Set the default ChandelierStrategy (used for per-position hint application).
    pub fn set_default_chandelier(&mut self, strat: ChandelierStrategy) {
        self.default_chandelier = strat;
    }

    /// Book 39: Register per-strategy Chandelier override.
    pub fn register_strategy_override(&mut self, name: String, strategy: ChandelierStrategy) {
        self.per_strategy_overrides.insert(name, strategy);
    }

    pub fn with_default_chandelier() -> Self {
        Self::new(
            ExitConfig::default(),
            Box::new(ChandelierStrategy::default()),
        )
    }

    /// Q-051: Construct with round-trip fee from unified cost config.
    pub fn with_costs(round_trip_fee_pct: f64) -> Self {
        let strategy = ChandelierStrategy { round_trip_fee_pct, ..ChandelierStrategy::default() };
        Self::new(ExitConfig::default(), Box::new(strategy))
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

    /// Sprint G: Check volume exhaustion. Returns Some(tight_atr) if RVOL signals
    /// climactic volume (exhaustion), meaning the stop should tighten dramatically.
    pub fn check_exhaustion(&self, current_rvol: f64) -> Option<f64> {
        self.strategy.check_exhaustion(current_rvol)
    }

    /// Evaluate ALL exit conditions for a position on the current tick.
    /// Returns the highest-priority exit that fires, or None.
    /// Priority: HALT > HardStop > Chandelier > TimeStop > EOD > Signal.
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
        exchange: &str,
    ) -> Option<ExitResult> {
        // S3: Active trading minutes from tick counter (halt-safe — pauses when no ticks arrive).
        // Each update_tracking() call increments active_trading_ticks. At ~5s per tick, 12 ticks ≈ 1 minute.
        let active_trading_minutes = position.active_trading_ticks / 12;
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
        // Gap-down protection: if price gapped THROUGH stop (current_price < stop_price),
        // fire MarketSell immediately — don't wait for a limit fill that may never come.
        if current_price <= position.stop_price {
            let gapped_through = current_price < position.stop_price;
            checks.push(ExitCheck {
                reason: ExitReason::HardStopLoss,
                priority: ExitPriority::HardStopLoss,
                order_type: if gapped_through {
                    // Stop breached by gap/slippage — emergency market sell
                    ExitOrderType::MarketSell
                } else {
                    ExitOrderType::LimitAtStop
                },
                limit_price: if gapped_through { None } else { Some(position.stop_price) },
                tif: if gapped_through { TimeInForce::Ioc } else { TimeInForce::Day },
            });
        }

        // Priority 3: Chandelier trailing stop
        // P21: Skip Chandelier evaluation if position is in carry state (stops frozen)
        if !is_carried {
            let chandelier_stop = self
                .strategy
                .compute_stop(position, position.highest_high, atr);
            if current_price <= chandelier_stop && chandelier_stop > position.stop_price {
                let gapped_through = current_price < chandelier_stop;
                checks.push(ExitCheck {
                    reason: ExitReason::ChandelierTrailing,
                    priority: ExitPriority::ChandelierStop,
                    order_type: if gapped_through {
                        // Stop breached by gap/slippage — emergency market sell
                        ExitOrderType::MarketSell
                    } else {
                        ExitOrderType::LimitAtStop
                    },
                    limit_price: if gapped_through { None } else { Some(chandelier_stop) },
                    tif: if gapped_through { TimeInForce::Ioc } else { TimeInForce::Day },
                });
            }
        }

        // Book 39: PARTIAL PROFIT LADDERING — 25% at Rung 3, 25% at Rung 4.
        // Remaining 50% trails with the Chandelier stop.
        // partial_exits_done: 0 = none, 1 = rung3 done, 2 = rung3+rung4 done
        if position.qty > 1 && !is_halt_flatten {
            let profit_pct = if position.avg_entry > 0.0 {
                (current_price - position.avg_entry) / position.avg_entry
            } else {
                0.0
            };
            // Rung 3 partial (25%): profit >= 1.5% and not yet done
            if position.trailing_rung >= 3 && position.partial_exits_done == 0 && profit_pct >= 0.015 {
                let partial = (position.qty as f64 * 0.25).max(1.0) as u32;
                if partial > 0 && partial < position.qty {
                    checks.push(ExitCheck {
                        reason: ExitReason::PartialProfitTake,
                        priority: ExitPriority::TimeStop, // Low priority — doesn't override protective stops
                        order_type: ExitOrderType::LimitAtStop,
                        limit_price: Some(current_price),
                        tif: TimeInForce::Day,
                    });
                    // Store the partial qty hint — engine.rs reads this
                    // Note: we can't mutate position here, engine.rs handles qty reduction
                }
            }
            // Rung 4 partial (25%): profit >= 2.5% and only rung3 done
            else if position.trailing_rung >= 4 && position.partial_exits_done == 1 && profit_pct >= 0.025 {
                let remaining = position.qty;
                let partial = (remaining as f64 * 0.33).max(1.0) as u32; // 33% of remaining ≈ 25% of original
                if partial > 0 && partial < remaining {
                    checks.push(ExitCheck {
                        reason: ExitReason::PartialProfitTake,
                        priority: ExitPriority::TimeStop,
                        order_type: ExitOrderType::LimitAtStop,
                        limit_price: Some(current_price),
                        tif: TimeInForce::Day,
                    });
                }
            }
        }

        // Priority 2.7: MAX HOLD HOURS TIME-STOP (Book 39/94)
        // Force exit if position held past strategy-specific max holding period.
        if let Some(max_hours) = position.max_hold_hours {
            let hold_hours = position.active_trading_ticks as f64 / 720.0; // 720 ticks/hour at 5s
            if hold_hours > max_hours {
                checks.push(ExitCheck {
                    reason: ExitReason::TimeStop,
                    priority: ExitPriority::TimeStop,
                    order_type: ExitOrderType::MarketSell,
                    limit_price: None,
                    tif: TimeInForce::Day,
                });
            }
        }

        // Priority 2.6: URGENCY RAMP — progressively tighten stops after ramp_hours (Book 39/94).
        // Between ramp_hours and max_hold_hours, linearly reduce the trailing ATR multiplier
        // from 1.0x to 0.3x. This accelerates exits on positions approaching their time limit
        // without the cliff-edge of a hard time-stop.
        if !is_carried
            && let (Some(ramp_hours), Some(max_hours)) = (position.exit_urgency_ramp_hours, position.max_hold_hours) {
                let hold_hours = position.active_trading_ticks as f64 / 720.0;
                if hold_hours > ramp_hours && hold_hours <= max_hours {
                    // Linear ramp from 1.0 at ramp_hours to 0.3 at max_hours
                    let ramp_span = (max_hours - ramp_hours).max(0.1);
                    let urgency_frac = ((hold_hours - ramp_hours) / ramp_span).clamp(0.0, 1.0);
                    let urgency_mult = 1.0 - urgency_frac * 0.7; // 1.0 → 0.3
                    let urgency_stop = position.highest_high - urgency_mult * atr;
                    if current_price <= urgency_stop && urgency_stop > position.stop_price {
                        checks.push(ExitCheck {
                            reason: ExitReason::TimeStop,
                            priority: ExitPriority::TimeStop,
                            order_type: ExitOrderType::LimitAtStop,
                            limit_price: Some(urgency_stop),
                            tif: TimeInForce::Day,
                        });
                    }
                }
            }

        // Priority 2.5: Time-stop — if position hasn't reached rung 2 within max_minutes,
        // tighten stop aggressively to force exit. Prevents capital lock in sideways trades.
        if self.config.time_stop_enabled
            && !is_carried
            && active_trading_minutes >= self.config.time_stop_max_minutes_to_rung2
            && position.trailing_rung < 2
        {
            // Aggressive trailing stop: use tight ATR multiplier below highest high
            let aggressive_stop = position.highest_high
                - self.config.time_stop_aggressive_trail_atr * atr.max(position.avg_entry * 0.005);
            if current_price <= aggressive_stop {
                checks.push(ExitCheck {
                    reason: ExitReason::TimeStop,
                    priority: ExitPriority::TimeStop,
                    order_type: ExitOrderType::MarketSell,
                    limit_price: None,
                    tif: TimeInForce::Day,
                });
            }
        }

        // Priority 2: EOD flatten — per-exchange cutoff (MEDIUM-1 fix)
        let eod_cutoff = self.config.eod_flatten_for_exchange(exchange);
        if time_secs >= eod_cutoff {
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

        // Book 39: Compute partial_qty for profit laddering exits
        let partial_qty = if winner.reason == ExitReason::PartialProfitTake {
            if position.partial_exits_done == 0 {
                // Rung 3: sell 25% of position
                Some((position.qty as f64 * 0.25).max(1.0) as u32)
            } else if position.partial_exits_done == 1 {
                // Rung 4: sell 33% of remaining (≈25% of original)
                Some((position.qty as f64 * 0.33).max(1.0) as u32)
            } else {
                None // Already did both partials
            }
        } else {
            None
        };

        Some(ExitResult {
            signal: ExitSignal {
                ticker_id: position.ticker_id,
                reason: winner.reason,
                priority: winner.priority,
                order_type,
                position_order_id: position.origin_order_id.clone(),
                limit_price,
                partial_qty,
            },
            tif,
            suppressed_count: checks.len() - 1,
        })
    }

    /// Update position tracking on each tick. Call BEFORE evaluate.
    /// Updates highest_high (H70), rung, and stop price (H68: ratchet UP only).
    /// Returns Some((old_rung, new_rung)) if rung advanced, None otherwise.
    /// Caller should write RungAdvanced WAL event when Some is returned.
    pub fn update_tracking(&self, position: &mut PositionState, current_price: f64, atr: f64) -> Option<(u8, u8)> {
        // S3: Increment active trading tick counter (halt-safe — only counts ticks that actually arrive)
        position.active_trading_ticks = position.active_trading_ticks.saturating_add(1);
        // Update highest_high
        if current_price > position.highest_high {
            position.highest_high = current_price;
        }
        // Book 39: Use per-strategy override if available, else global strategy.
        // When per-position signal hints exist (rung3_atr, trail_bias), apply them
        // via with_signal_hints() to create a position-specific Chandelier config.
        let has_hints = position.suggested_rung3_atr.is_some()
            || position.exit_trail_bias.is_some()
            || position.suggested_initial_stop_atr_mult.is_some();
        let hinted;
        let strat: &dyn ExitStrategy = if let Some(ovr) = self.per_strategy_overrides.get(&position.entry_type) {
            if has_hints {
                hinted = ovr.with_signal_hints(
                    position.suggested_initial_stop_atr_mult,
                    position.suggested_rung3_atr,
                    position.exit_trail_bias.as_deref(),
                );
                &hinted as &dyn ExitStrategy
            } else {
                ovr as &dyn ExitStrategy
            }
        } else if has_hints {
            // Default strategy path but position has signal hints — apply to default_chandelier
            hinted = self.default_chandelier.with_signal_hints(
                position.suggested_initial_stop_atr_mult,
                position.suggested_rung3_atr,
                position.exit_trail_bias.as_deref(),
            );
            &hinted as &dyn ExitStrategy
        } else {
            // Default strategy, no hints — use as-is (may be InfiniteChandelier)
            self.strategy.as_ref()
        };
        // Compute new rung (can only increase)
        let new_rung = strat.compute_rung(position, position.highest_high, atr);
        let rung_advanced = if new_rung > position.trailing_rung {
            let old = position.trailing_rung;
            position.trailing_rung = new_rung;
            Some((old, new_rung))
        } else {
            None
        };
        // Compute new stop (H68: can NEVER decrease)
        let new_stop = strat.compute_stop(position, position.highest_high, atr);
        if new_stop > position.stop_price {
            position.stop_price = new_stop;
        }
        rung_advanced
    }

    /// Price spike filter (H71): detect if a price drop is likely a spike artifact.
    /// Returns true if the drop looks like a spike (should NOT trigger stop).
    pub fn is_price_spike(&self, prev_price: f64, current_price: f64, bid: f64, ask: f64) -> bool {
        if prev_price <= 0.0 {
            return false;
        }
        // Guard: invalid bid/ask → cannot verify spike, assume not a spike
        if bid <= 0.0 {
            return false;
        }
        if ask <= 0.0 {
            return false;
        }
        // Guard: crossed book (ask <= bid) → stale/corrupt quote data
        if ask <= bid {
            eprintln!("WARN: crossed book bid={} ask={}", bid, ask);
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
                partial_qty: None,
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

    /// Sprint 6: Update all multipliers from config-driven parameters.
    pub fn update_volatility_cfg(&mut self, realized_vol_ann: f64, cfg: &AdaptiveConfig) {
        let range_span = cfg.volatility_range[1] - cfg.volatility_range[0];
        let vol_span = cfg.volatility_ann_high - cfg.volatility_ann_low;
        let raw = cfg.volatility_range[0] + (realized_vol_ann - cfg.volatility_ann_low) * (range_span / vol_span);
        self.volatility = raw.clamp(cfg.volatility_range[0], cfg.volatility_range[1]);
    }

    pub fn update_time_decay_cfg(&mut self, time_fraction: f64, cfg: &AdaptiveConfig) {
        let raw = cfg.time_decay_range[1] - cfg.time_decay_slope * time_fraction;
        self.time_decay = raw.clamp(cfg.time_decay_range[0], cfg.time_decay_range[1]);
    }

    pub fn update_momentum_cfg(&mut self, momentum_pct: f64, cfg: &AdaptiveConfig) {
        let raw = 1.0 + momentum_pct.abs() * cfg.momentum_sensitivity;
        self.momentum = raw.clamp(cfg.momentum_range[0], cfg.momentum_range[1]);
    }

    pub fn update_liquidity_cfg(&mut self, amihud: f64, cfg: &AdaptiveConfig) {
        let raw = 1.0 + amihud * cfg.liquidity_sensitivity;
        self.liquidity = raw.clamp(cfg.liquidity_range[0], cfg.liquidity_range[1]);
    }

    pub fn update_heat_cfg(&mut self, heat_pct: f64, cfg: &AdaptiveConfig) {
        let heat_span = cfg.heat_high_pct - cfg.heat_low_pct;
        let range_span = cfg.heat_range[1] - cfg.heat_range[0];
        let raw = cfg.heat_range[1] - (heat_pct - cfg.heat_low_pct).max(0.0) * (range_span / heat_span);
        self.heat = raw.clamp(cfg.heat_range[0], cfg.heat_range[1]);
    }

    pub fn update_regime_cfg(&mut self, is_reduce: bool, cfg: &AdaptiveConfig) {
        self.regime = if is_reduce { cfg.regime_reduce_mult } else { 1.0 };
    }

    pub fn update_mega_runner_cfg(&mut self, profit_atr: f64, cfg: &AdaptiveConfig) {
        if profit_atr > cfg.mega_runner_threshold_atr {
            let raw = 1.0 + (profit_atr - cfg.mega_runner_threshold_atr) * cfg.mega_runner_slope;
            self.mega_runner = raw.clamp(cfg.mega_runner_range[0], cfg.mega_runner_range[1]);
        } else {
            self.mega_runner = 1.0;
        }
    }

    /// Legacy methods for backward compatibility (use hardcoded defaults).
    pub fn update_volatility(&mut self, realized_vol_ann: f64) {
        self.update_volatility_cfg(realized_vol_ann, &AdaptiveConfig::default());
    }
    pub fn update_time_decay(&mut self, time_fraction: f64) {
        self.update_time_decay_cfg(time_fraction, &AdaptiveConfig::default());
    }
    pub fn update_momentum(&mut self, momentum_pct: f64) {
        self.update_momentum_cfg(momentum_pct, &AdaptiveConfig::default());
    }
    pub fn update_liquidity(&mut self, amihud: f64) {
        self.update_liquidity_cfg(amihud, &AdaptiveConfig::default());
    }
    pub fn update_heat(&mut self, heat_pct: f64) {
        self.update_heat_cfg(heat_pct, &AdaptiveConfig::default());
    }
    pub fn update_regime(&mut self, is_reduce: bool) {
        self.update_regime_cfg(is_reduce, &AdaptiveConfig::default());
    }
    pub fn update_mega_runner(&mut self, profit_atr: f64) {
        self.update_mega_runner_cfg(profit_atr, &AdaptiveConfig::default());
    }
}

/// Sprint 6: Adaptive multiplier configuration (from config.toml [chandelier.adaptive]).
#[derive(Clone, Debug)]
pub struct AdaptiveConfig {
    pub volatility_range: [f64; 2],
    pub volatility_ann_low: f64,
    pub volatility_ann_high: f64,
    pub time_decay_range: [f64; 2],
    pub time_decay_slope: f64,
    pub momentum_range: [f64; 2],
    pub momentum_sensitivity: f64,
    pub liquidity_range: [f64; 2],
    pub liquidity_sensitivity: f64,
    pub heat_range: [f64; 2],
    pub heat_low_pct: f64,
    pub heat_high_pct: f64,
    pub regime_reduce_mult: f64,
    pub mega_runner_threshold_atr: f64,
    pub mega_runner_slope: f64,
    pub mega_runner_range: [f64; 2],
}

impl Default for AdaptiveConfig {
    fn default() -> Self {
        Self {
            volatility_range: [0.8, 1.5],
            volatility_ann_low: 0.20,
            volatility_ann_high: 0.50,
            time_decay_range: [0.8, 1.0],
            time_decay_slope: 0.2,
            momentum_range: [1.0, 1.3],
            momentum_sensitivity: 10.0,
            liquidity_range: [1.0, 1.4],
            liquidity_sensitivity: 40.0,
            heat_range: [0.7, 1.0],
            heat_low_pct: 2.0,
            heat_high_pct: 8.0,
            regime_reduce_mult: 0.6,
            mega_runner_threshold_atr: 3.0,
            mega_runner_slope: 0.2,
            mega_runner_range: [1.0, 2.0],
        }
    }
}

/// Infinite Chandelier: Chandelier with 8 adaptive multipliers.
/// The effective trail distance = base_rung5_trail × combined_multiplier.
#[derive(Default)]
pub struct InfiniteChandelier {
    pub base: ChandelierStrategy,
    pub multipliers: AdaptiveMultipliers,
    pub adaptive_config: AdaptiveConfig,
}

impl InfiniteChandelier {
    pub fn new() -> Self {
        Self {
            base: ChandelierStrategy::default(),
            multipliers: AdaptiveMultipliers::default(),
            adaptive_config: AdaptiveConfig::default(),
        }
    }

    /// Compute adaptive trailing stop for rung 5 (mega-runner path).
    pub fn adaptive_trail(&self, high: f64, atr: f64) -> f64 {
        let effective_trail = self.base.rung5_trail_atr * self.multipliers.combined();
        high - effective_trail * atr
    }
}

impl ExitStrategy for InfiniteChandelier {
    fn compute_stop(&self, pos: &PositionState, high: f64, atr: f64) -> f64 {
        // ATR floor: minimum atr_floor_pct of entry price to prevent zombie positions when ATR=0 on cold start
        let atr = atr.max(pos.avg_entry * self.base.atr_floor_pct);
        let rung = self.compute_rung(pos, high, atr);
        match rung {
            // Rungs 0-4: delegate to base ChandelierStrategy (same new logic)
            0 => pos.stop_price,
            1 => pos.avg_entry - self.base.initial_stop_atr_mult * atr,
            2 => {
                let fee_amount = pos.avg_entry * self.base.round_trip_fee_pct;
                pos.avg_entry + fee_amount
            }
            3 => high - self.base.rung3_trail_atr * atr,
            4 => high - self.base.rung4_trail_atr * atr,
            _ => {
                // Rung 5+: adaptive trailing with multipliers applied to rung5 trail
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

    fn set_initial_stop_atr(&mut self, mult: f64) {
        self.base.initial_stop_atr_mult = mult;
    }

    fn update_multipliers(&mut self, vol: f64, time_frac: f64, momentum: f64,
                          amihud: f64, heat: f64, is_reduce: bool) {
        let cfg = &self.adaptive_config;
        self.multipliers.update_volatility_cfg(vol, cfg);
        self.multipliers.update_time_decay_cfg(time_frac, cfg);
        self.multipliers.update_momentum_cfg(momentum, cfg);
        self.multipliers.update_liquidity_cfg(amihud, cfg);
        self.multipliers.update_heat_cfg(heat, cfg);
        self.multipliers.update_regime_cfg(is_reduce, cfg);
    }

    fn update_mega_runner(&mut self, profit_atr: f64) {
        let cfg = &self.adaptive_config;
        self.multipliers.update_mega_runner_cfg(profit_atr, cfg);
    }

    fn check_exhaustion(&self, current_rvol: f64) -> Option<f64> {
        // Sprint G: Delegate exhaustion check to base ChandelierStrategy
        self.base.check_exhaustion(current_rvol)
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
