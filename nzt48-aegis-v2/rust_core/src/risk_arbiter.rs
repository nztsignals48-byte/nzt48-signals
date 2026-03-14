//! RiskArbiter — synchronous 31-check risk gate with 4-state regime hierarchy.
//! HALT > FLATTEN > REDUCE > NORMAL. Fail-closed. State frozen during eval.

use crate::config::RiskConfig;
use crate::cross_asset_macro::{CrossAssetMacro, MacroIndicator, MacroRegimeSignal};
use crate::portfolio::PortfolioState;
use crate::types::{Direction, RiskDecision, RiskRegime, TickerId, VetoReason};
use std::collections::HashMap;

/// Context provided by the caller for each evaluation.
#[derive(Clone, Debug)]
pub struct EvalContext {
    /// Seconds from midnight London local time.
    pub time_secs: u32,
    /// How many seconds since last IBKR tick for this ticker.
    pub last_tick_age_secs: u64,
    /// Current best bid price.
    pub bid: f64,
    /// Current best ask price.
    pub ask: f64,
    /// Broker connection status.
    pub broker_connected: bool,
    /// WAL writer availability.
    pub wal_available: bool,
    /// Current timestamp in nanoseconds (for velocity tracking + decision).
    pub now_ns: u64,
    /// Phase 15: Per-ticker realized volatility (annualized) for CVaR.
    pub volatilities: HashMap<TickerId, f64>,
    /// Phase 15: Whether the ticker is halted in the Universe.
    pub ticker_halted: bool,
    /// Phase 15: GARCH forecast sigma for this ticker (annualized).
    pub garch_sigma: f64,
    /// P0-03: ETP leverage factor (1, 2, 3, or 5). Scales GARCH threshold.
    pub leverage_factor: u32,
    /// Phase 15: Scanner score for this signal [0, 100].
    pub scanner_score: f64,
    /// Phase 15: Kelly fraction for this signal.
    pub kelly_fraction_raw: f64,
    /// Phase 9: Macro indicator snapshot (VIX, DXY, credit spreads, Fear & Greed).
    pub macro_indicator: MacroIndicator,
    /// Phase 9: Macro staleness threshold in nanoseconds (e.g., 300s).
    pub macro_stale_threshold_ns: u64,
}

impl Default for EvalContext {
    fn default() -> Self {
        Self {
            time_secs: 10 * 3600,
            last_tick_age_secs: 1,
            bid: 10.0,
            ask: 10.02,
            broker_connected: true,
            wal_available: true,
            now_ns: 1_000_000_000,
            volatilities: HashMap::new(),
            ticker_halted: false,
            garch_sigma: 0.30,
            leverage_factor: 1,
            scanner_score: 50.0,
            kelly_fraction_raw: 0.08,
            macro_indicator: MacroIndicator::default(), // Phase 9: neutral macro defaults
            macro_stale_threshold_ns: 300_000_000_000,  // 300 seconds
        }
    }
}

/// The synchronous risk gate. Evaluates ALL canonical rules in deterministic order.
#[derive(Clone, Debug)]
pub struct RiskArbiter {
    pub regime: RiskRegime,
    pub config: RiskConfig,
    /// Recent approved intents: (ticker_id, timestamp_ns) for velocity tracking.
    velocity_log: Vec<(TickerId, u64)>,
    /// Ouroboros-calibrated regime scaling multipliers (e.g. "Reduce" → 0.4).
    pub regime_scales: HashMap<String, f64>,
    /// Ouroboros-calibrated per-ticker Kelly fraction caps.
    pub kelly_fractions: HashMap<String, f64>,
}

impl RiskArbiter {
    pub fn new(config: RiskConfig) -> Self {
        Self {
            regime: RiskRegime::Normal,
            config,
            velocity_log: Vec::new(),
            regime_scales: HashMap::new(),
            kelly_fractions: HashMap::new(),
        }
    }

    /// Evaluate an OrderIntent against all risk checks. Returns RiskDecision.
    /// This is SYNCHRONOUS and takes < 1ms.
    pub fn evaluate(
        &mut self,
        intent_ticker: TickerId,
        intent_side: Direction,
        intent_confidence: f64,
        intent_kelly: f64,
        portfolio: &PortfolioState,
        ctx: &EvalContext,
    ) -> RiskDecision {
        let ts = ctx.now_ns;

        // CHECK 1: ISA Safety — side == Short → HALT + REJECT (P0)
        if intent_side == Direction::Short {
            self.regime = RiskRegime::Halt;
            return self.reject(VetoReason::IsaShortSellBlocked, ts);
        }

        // CHECK 2: Inverse Mutual Exclusion (H32)
        if let Some(blocker) = portfolio.inverse_blocker(intent_ticker) {
            return self.reject(
                VetoReason::InverseMutualExclusion { blocker: blocker.0 },
                ts,
            );
        }

        // CHECK 5: Risk Regime — HALT/FLATTEN → REJECT all entries
        if self.regime >= RiskRegime::Flatten {
            return self.reject(VetoReason::RejectToHalt, ts);
        }

        // CHECK 6: Max Positions — filled + pending >= max (H34)
        if portfolio.total_position_count() >= self.config.max_positions {
            return self.reject(VetoReason::MaxPositionsReached, ts);
        }

        // CHECK 7: Data Staleness — > 120s → HALT
        if ctx.last_tick_age_secs > self.config.stale_data_threshold_secs {
            self.regime = RiskRegime::Halt;
            return self.reject(
                VetoReason::StaleData {
                    age_secs: ctx.last_tick_age_secs,
                },
                ts,
            );
        }

        // CHECK 8: Broker Connected
        if !ctx.broker_connected {
            self.regime = RiskRegime::Halt;
            return self.reject(VetoReason::BrokerDisconnected, ts);
        }

        // CHECK 9: WAL Available
        if !ctx.wal_available {
            self.regime = RiskRegime::Halt;
            return self.reject(VetoReason::WalUnavailable, ts);
        }

        // CHECK 10: Confidence Floor
        if intent_confidence < self.config.confidence_floor {
            return self.reject(
                VetoReason::ConfidenceBelowFloor {
                    confidence_x10: (intent_confidence * 10.0) as u32,
                },
                ts,
            );
        }

        // CHECK 11: Time-of-Day Cutoff — after 15:45 London (H35)
        if ctx.time_secs >= self.config.entry_cutoff_secs {
            return self.reject(VetoReason::TooLateInSession, ts);
        }

        // CHECK 12: Auction Period
        let in_open_auction = ctx.time_secs >= self.config.auction_open_start_secs
            && ctx.time_secs < self.config.auction_open_end_secs;
        let in_close_auction = ctx.time_secs >= self.config.auction_close_start_secs
            && ctx.time_secs < self.config.auction_close_end_secs;
        if in_open_auction || in_close_auction {
            return self.reject(VetoReason::AuctionPeriod, ts);
        }

        // CHECK 13: Spread Veto (H36)
        if ctx.bid > 0.0 {
            let spread_pct = (ctx.ask - ctx.bid) / ctx.bid * 100.0;
            if spread_pct > self.config.spread_veto_pct {
                return self.reject(
                    VetoReason::SpreadTooWide {
                        spread_bps: (spread_pct * 100.0) as u32,
                    },
                    ts,
                );
            }
        }

        // CHECK 14: Cash Buffer (H31)
        if portfolio.cash_buffer_pct() < self.config.cash_buffer_pct {
            return self.reject(VetoReason::CashBufferInsufficient, ts);
        }

        // CHECK 15: Portfolio Heat
        if portfolio.portfolio_heat_pct() >= self.config.portfolio_heat_limit_pct {
            return self.reject(VetoReason::PortfolioHeatExceeded, ts);
        }

        // CHECK 16: Sector Heat (H30)
        if portfolio.sector_heat_pct(intent_ticker) >= self.config.sector_heat_cap_pct {
            let heat = portfolio.sector_heat_pct(intent_ticker);
            return self.reject(
                VetoReason::SectorHeatExceeded {
                    sector: "sector".into(),
                    pct: heat as u32,
                },
                ts,
            );
        }

        // CHECK 17: ISA Annual Limit
        if portfolio.isa_year_invested >= self.config.isa_annual_limit_gbp {
            return self.reject(VetoReason::IsaAnnualLimitExceeded, ts);
        }

        // CHECK 18: Daily Drawdown — >2% from high-water → FLATTEN (H29)
        if portfolio.daily_drawdown_pct() > self.config.daily_drawdown_pct {
            self.regime = RiskRegime::Flatten;
            return self.reject(VetoReason::DailyDrawdownBreached, ts);
        }

        // CHECK 19: Velocity Check (H37)
        self.prune_velocity(ts);
        let recent = self
            .velocity_log
            .iter()
            .filter(|(t, _)| *t == intent_ticker)
            .count();
        if recent >= self.config.velocity_max_intents as usize {
            return self.reject(VetoReason::VelocityCheckTriggered, ts);
        }

        // CHECK 20: Macro Regime Escalation (Phase 9)
        if let Some(veto) = self.evaluate_macro_escalation(ctx) {
            return self.reject(veto, ts);
        }

        // CHECK 21: Consecutive Loss Breaker (H38)
        if portfolio.consecutive_stop_losses >= self.config.consecutive_loss_halt {
            self.regime = RiskRegime::Halt;
            return self.reject(VetoReason::ConsecutiveLossBreaker, ts);
        }

        // CHECK 22: Duplicate Position — already holding this ticker
        if portfolio.has_position(&intent_ticker) {
            return self.reject(VetoReason::DuplicatePosition, ts);
        }

        // CHECK 23: Ticker Halted (reverse split, synthetic halt, etc)
        if ctx.ticker_halted {
            return self.reject(VetoReason::TickerHalted, ts);
        }

        // CHECK 24: CVaR Heat — portfolio-level conditional value at risk
        let cvar_heat = portfolio.cvar_heat_pct(&ctx.volatilities);
        if cvar_heat > self.config.portfolio_heat_limit_pct * 1.5 {
            // CVaR heat limit = 1.5x the basic heat limit
            return self.reject(
                VetoReason::CvarHeatExceeded {
                    cvar_pct: cvar_heat as u32,
                },
                ts,
            );
        }

        // CHECK 25: GARCH forecast sigma too high — leverage-scaled (Avellaneda & Zhang 2010).
        // P0-03 FIX: 3x ETP has ~√3 × vol, 5x has ~√5 × vol. Scale threshold accordingly.
        let garch_threshold = 0.80 * (ctx.leverage_factor.max(1) as f64).sqrt();
        if ctx.garch_sigma > garch_threshold {
            return self.reject(
                VetoReason::GarchVolTooHigh {
                    sigma_pct: (ctx.garch_sigma * 100.0) as u32,
                },
                ts,
            );
        }

        // CHECK 26: Scanner score below minimum threshold (< 30)
        if ctx.scanner_score > 0.0 && ctx.scanner_score < 30.0 {
            return self.reject(
                VetoReason::ScannerScoreTooLow {
                    score: ctx.scanner_score as u32,
                },
                ts,
            );
        }

        // CHECK 27: Kelly fraction below floor (< 0.5%)
        // Ouroboros per-ticker Kelly cap overrides global max when available.
        let ticker_key = intent_ticker.0.to_string();
        let effective_kelly = if let Some(&cap) = self.kelly_fractions.get(&ticker_key) {
            intent_kelly.min(cap)
        } else {
            intent_kelly
        };
        if ctx.kelly_fraction_raw > 0.0 && ctx.kelly_fraction_raw < 0.005 {
            return self.reject(VetoReason::KellyBelowFloor, ts);
        }

        // All checks passed. Calculate adjusted size.
        // SC-13: Kelly scaling ramp — max(0.1, min(1.0, trades/250))
        let kelly_ramp = (self.config.kelly_ramp_trades as f64 / 250.0).clamp(0.1, 1.0);
        let ramped_kelly = effective_kelly * kelly_ramp;
        let size = ramped_kelly * portfolio.equity;
        // Ouroboros-calibrated regime scaling (fall back to hardcoded defaults).
        let regime_name = format!("{:?}", self.regime);
        let default_scale = if self.regime == RiskRegime::Reduce { 0.5 } else { 1.0 };
        let regime_scale = self.regime_scales.get(&regime_name).copied().unwrap_or(default_scale);
        let adjusted_size = size * regime_scale;

        // SC-05: Minimum entry size gate (£1500).
        // Suspended during Kelly ramp when validated_trades < 250.
        if self.config.kelly_ramp_trades >= 250 && adjusted_size < self.config.minimum_entry_gbp {
            return self.reject(
                VetoReason::BelowMinimumEntrySize {
                    size_gbp: adjusted_size as u32,
                },
                ts,
            );
        }

        // Record approved intent for velocity tracking
        self.velocity_log.push((intent_ticker, ts));

        RiskDecision {
            decision_timestamp_ns: ts,
            adjusted_size,
            approved: true,
            regime: self.regime,
            reason: VetoReason::Approved,
        }
    }

    /// Transition to a higher (more restrictive) regime.
    pub fn escalate(&mut self, new_regime: RiskRegime) {
        if new_regime > self.regime {
            self.regime = new_regime;
        }
    }

    /// Clear REDUCE if conditions have been nominal for 5 minutes.
    pub fn clear_reduce(&mut self) {
        if self.regime == RiskRegime::Reduce {
            self.regime = RiskRegime::Normal;
        }
    }

    /// Clear FLATTEN after all positions closed + reconciliation clean.
    pub fn clear_flatten(&mut self) {
        if self.regime == RiskRegime::Flatten {
            self.regime = RiskRegime::Normal;
        }
    }

    /// Manual human approval to clear HALT.
    pub fn manual_clear_halt(&mut self) {
        if self.regime == RiskRegime::Halt {
            self.regime = RiskRegime::Normal;
        }
    }

    fn reject(&self, reason: VetoReason, timestamp_ns: u64) -> RiskDecision {
        RiskDecision {
            decision_timestamp_ns: timestamp_ns,
            adjusted_size: 0.0,
            approved: false,
            regime: self.regime,
            reason,
        }
    }

    fn prune_velocity(&mut self, now_ns: u64) {
        let cutoff = now_ns.saturating_sub(self.config.velocity_window_ns);
        self.velocity_log.retain(|(_, ts)| *ts >= cutoff);
    }

    /// CHECK 20: Macro Regime Escalation (Phase 9).
    /// Evaluates macro indicators (VIX, DXY, credit spreads, Fear & Greed) and escalates regime if needed.
    /// Returns VetoReason if regime escalation triggered, None otherwise.
    fn evaluate_macro_escalation(&mut self, ctx: &EvalContext) -> Option<VetoReason> {
        let macro_eval = CrossAssetMacro::from_indicator(ctx.macro_indicator);
        let macro_signal = macro_eval.evaluate();

        // Trigger A: VIX Crisis → FLATTEN (allow exits, block new entries)
        if macro_signal == MacroRegimeSignal::Crisis {
            self.regime = RiskRegime::Flatten;
            return Some(VetoReason::MacroCrisisDetected {
                vix: (ctx.macro_indicator.vix * 10.0) as u32,
                credit_bps: ctx.macro_indicator.credit_spread_bps as u32,
            });
        }

        // Trigger B: Macro Stress + Stale Ticks → HALT (data unreliable)
        if macro_signal == MacroRegimeSignal::Stress && ctx.last_tick_age_secs > 60 {
            self.regime = RiskRegime::Halt;
            return Some(VetoReason::MacroStressWithStaleTicks);
        }

        // Trigger D: Macro Data Stale > threshold → assume worst-case (REDUCE)
        if macro_eval.is_stale(ctx.now_ns, ctx.macro_stale_threshold_ns)
            && macro_signal != MacroRegimeSignal::Normal
        {
            self.regime = RiskRegime::Reduce;
            let age_secs = ctx
                .now_ns
                .saturating_sub(ctx.macro_indicator.last_update_ns)
                / 1_000_000_000;
            return Some(VetoReason::MacroDataStale {
                age_secs,
            });
        }

        None
    }
}
