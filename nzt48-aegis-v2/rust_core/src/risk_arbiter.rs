//! RiskArbiter — synchronous 36-check risk gate with 4-state regime hierarchy.
//! HALT > FLATTEN > REDUCE > NORMAL. Fail-closed. State frozen during eval.

use crate::config::RiskConfig;
use crate::cross_asset_macro::{CrossAssetMacro, MacroIndicator, MacroRegimeSignal};
use crate::portfolio::PortfolioState;
use crate::sector_rotation::sector_for_ticker;
use crate::types::{Direction, RiskDecision, RiskRegime, TickerId, VetoReason};
use std::collections::{HashMap, VecDeque};

// Sprint 6: All constants moved to config.toml [hardening] section.
// Accessed via self.config.* in RiskArbiter methods.
const VELOCITY_WINDOW_5MIN_NS: u64 = 300_000_000_000;

// ── Book 7: Session Exposure Limits ──
// Max NAV by session (% of current equity)
const SESSION_ASIA_LIMIT_PCT: f64 = 30.0;
const SESSION_EUROPE_LIMIT_PCT: f64 = 50.0;
const SESSION_US_LIMIT_PCT: f64 = 60.0;
const SESSION_OVERLAP_EU_US_LIMIT_PCT: f64 = 80.0;

/// Session classification based on London local time (seconds from midnight).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TradingSession {
    Asia,       // 00:00-08:00 London time
    Europe,     // 08:00-14:30 London time
    Us,         // 14:30-21:00 London time (US core hours, EU still open until 15:30)
    Overlap,    // EU+US overlap: 14:30-15:30 (covered by Us above, split handled separately)
    PostMarket, // 21:00-00:00 London time
}

impl TradingSession {
    /// Determine current trading session from London local time (seconds from midnight).
    pub fn from_london_secs(london_secs: u32) -> Self {
        match london_secs {
            0..=28799 => TradingSession::Asia,         // 00:00-07:59:59
            28800..=52199 => TradingSession::Europe,   // 08:00-14:29:59
            52200..=75599 => TradingSession::Us,       // 14:30-20:59:59
            _ => TradingSession::PostMarket,            // 21:00-23:59:59 and beyond
        }
    }

    /// Return session name for logging.
    pub fn name(&self) -> &'static str {
        match self {
            TradingSession::Asia => "Asia",
            TradingSession::Europe => "Europe",
            TradingSession::Us => "US+EU_Overlap",
            TradingSession::Overlap => "EU+US_Overlap",
            TradingSession::PostMarket => "PostMarket",
        }
    }

    /// Classify ticker's exchange to a session.
    pub fn classify_exchange(exchange_mic: &str) -> TradingSession {
        match exchange_mic {
            // Asia: Tokyo (TSE), Hong Kong (HKEX), Singapore (SGX)
            "XTSE" | "XHKG" | "XSES" => TradingSession::Asia,

            // Europe: All ISA-eligible European exchanges
            "XLON" | "XDUB" |  // UK & Ireland (8:00-16:30 GMT/BST)
            "XETR" | "XPAR" | "XAMS" | "XBRU" | "XLIS" | "XMIL" | "XMAD" | // Euronext/CBF
            "XSWX" | "XSTO" | "XOSL" | "XCSE" | "XHEL" | "XWAR" => TradingSession::Europe,

            // US: NYSE (XNYS), NASDAQ (XNAS)
            "XNYS" | "XNAS" | "ARCX" | "BATS" => TradingSession::Us,

            // Unknown: default to Europe
            _ => TradingSession::Europe,
        }
    }
}

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
    /// Momentum re-entry: PredictiveScorer IC for this ticker.
    pub ticker_ic: f64,
    /// Momentum re-entry: PredictiveScorer trade count for this ticker.
    pub ticker_trade_count: u32,
    /// Momentum re-entry: PredictiveScorer locked status.
    pub ticker_locked: bool,
    /// Momentum re-entry: How many positions already held for this ticker.
    pub ticker_position_count: u32,
    // --- WIRED Sprint 2 (2026-03-21) ---
    /// Sprint 2B: GARCH EVT CVaR — tail risk expected shortfall for this ticker.
    pub evt_cvar: f64,
    /// Sprint 2A: Kalman divergence — (raw - smoothed) / smoothed. Positive = breakout signal.
    pub kalman_divergence: f64,
    /// Sprint 3A: Native spread in basis points before FX conversion.
    pub native_spread_bps: f64,
    /// Sprint 2D: Structural tradability score from Python bridge [0-100].
    pub structural_score: f64,
    /// Book 7: Exchange MIC code for the intent ticker (e.g., "XLON", "XETR").
    pub exchange_mic: String,
}

impl Default for EvalContext {
    /// SENTINEL DEFAULTS: dangerous placeholder values that force callers to set real values.
    /// garch_sigma=-1.0 triggers CHECK 25 (GARCH), scanner_score=-1.0 won't trigger CHECK 26,
    /// kelly_fraction_raw=0.0 won't trigger CHECK 27, last_tick_age_secs=999 triggers CHECK 7 (HALT).
    /// If you use `..EvalContext::default()` and forget a field, the risk arbiter will REJECT.
    fn default() -> Self {
        Self {
            time_secs: 10 * 3600,
            last_tick_age_secs: 999, // SENTINEL: triggers CHECK 7 (>120s = HALT) if not overridden
            bid: 10.0,
            ask: 10.02,
            broker_connected: true,
            wal_available: true,
            now_ns: 1_000_000_000,
            volatilities: HashMap::new(),
            ticker_halted: false,
            garch_sigma: -1.0,      // SENTINEL: triggers CHECK 25 (negative > threshold impossible, but safe)
            leverage_factor: 1,
            scanner_score: -1.0,     // SENTINEL: CHECK 26 requires >0 && <30, so -1 passes (safe: no false positives)
            kelly_fraction_raw: 0.0, // SENTINEL: CHECK 27 requires >0 && <0.005, so 0.0 passes (safe)
            macro_indicator: MacroIndicator::default(), // Phase 9: neutral macro defaults
            macro_stale_threshold_ns: 300_000_000_000,  // 300 seconds
            ticker_ic: 0.0,
            ticker_trade_count: 0,
            ticker_locked: false,
            ticker_position_count: 0,
            // Sprint 2 defaults: conservative (fail-closed)
            evt_cvar: 0.0,           // No tail risk data = no reduction (will be overridden)
            kalman_divergence: 0.0,  // No divergence data
            native_spread_bps: 0.0,  // No spread data
            structural_score: 50.0,  // Neutral default (won't trigger veto at 40 or penalty at 60)
            exchange_mic: String::new(),  // SENTINEL: empty exchange = unknown session (safe)
        }
    }
}

/// The synchronous risk gate. Evaluates ALL canonical rules in deterministic order.
#[derive(Clone, Debug)]
pub struct RiskArbiter {
    pub regime: RiskRegime,
    pub config: RiskConfig,
    /// Recent approved intents: (ticker_id, timestamp_ns) for velocity tracking.
    velocity_log: VecDeque<(TickerId, u64)>,
    /// Ouroboros-calibrated regime scaling multipliers (e.g. "Reduce" → 0.4).
    pub regime_scales: HashMap<String, f64>,
    /// Ouroboros-calibrated per-ticker Kelly fraction caps.
    pub kelly_fractions: HashMap<String, f64>,
    /// Simulation mode: relaxes cash buffer & portfolio heat checks for data collection.
    pub simulation_mode: bool,
    /// P2-3.3: When true AND simulation_mode=true, enforce live risk gates anyway.
    pub paper_uses_live_gates: bool,
    /// Ouroboros ticker blacklist: symbols with WR < 30% over 10+ trades.
    pub ticker_blacklist: Vec<String>,
    /// P1-2.7: VIX hysteresis state (prevents flip-flop at boundaries).
    pub vix_high: bool,
    pub vix_extreme: bool,
    /// P1-2.16: Rolling equity snapshots for drawdown velocity (timestamp_ns, equity).
    equity_snapshots: Vec<(u64, f64)>,
    /// Dynamic Universe rotation scores: symbol → final_score (0.0-1.5).
    /// Used to weight Kelly allocation: higher-scored symbols get more capital.
    pub rotation_scores: HashMap<String, f64>,
    /// Equity ratchet: track all-time HWM for drawdown-based Kelly scaling.
    pub equity_hwm: f64,
    /// Sprint 7: Per-exchange entry cutoffs in seconds from midnight London time.
    /// Loaded from config.toml [timing.exchange_cutoffs]. Keys are exchange names (e.g. "LSE", "XETR").
    /// If a ticker's exchange has a cutoff here, it overrides the global entry_cutoff_secs.
    pub exchange_cutoffs_secs: HashMap<String, u32>,
}

impl RiskArbiter {
    pub fn new(config: RiskConfig) -> Self {
        Self {
            regime: RiskRegime::Normal,
            config,
            velocity_log: VecDeque::new(),
            regime_scales: HashMap::new(),
            kelly_fractions: HashMap::new(),
            simulation_mode: false,
            paper_uses_live_gates: true, // BUG-006 FIX: default to true so backtests/replay enforce risk gates
            ticker_blacklist: Vec::new(),
            vix_high: false,
            vix_extreme: false,
            equity_snapshots: Vec::new(),
            rotation_scores: HashMap::new(),
            equity_hwm: 0.0,
            exchange_cutoffs_secs: HashMap::new(),
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
        // P2-3.3: Enforce live gates even in paper mode when paper_uses_live_gates=true.
        let enforce_live_gates = !self.simulation_mode || self.paper_uses_live_gates;

        // Phase 0.3: Checks reordered by rejection frequency. Cheapest/most-common
        // rejections first to short-circuit before expensive portfolio lookups.

        // CHECK 8: Broker Connected — most frequent rejection (weekend, disconnect)
        if !ctx.broker_connected {
            self.regime = RiskRegime::Halt;
            return self.reject(VetoReason::BrokerDisconnected, ts);
        }

        // CHECK 7: Data Staleness — > 120s → HALT (second most frequent)
        if ctx.last_tick_age_secs > self.config.stale_data_threshold_secs {
            self.regime = RiskRegime::Halt;
            return self.reject(
                VetoReason::StaleData {
                    age_secs: ctx.last_tick_age_secs,
                },
                ts,
            );
        }

        // CHECK 9: WAL Available
        if !ctx.wal_available {
            self.regime = RiskRegime::Halt;
            return self.reject(VetoReason::WalUnavailable, ts);
        }

        // CHECK 5: Risk Regime — HALT/FLATTEN → REJECT all entries
        if self.regime >= RiskRegime::Flatten {
            return self.reject(VetoReason::RejectToHalt, ts);
        }

        // CHECK 1: ISA Safety — side == Short → HALT + REJECT (P0)
        if intent_side == Direction::Short {
            self.regime = RiskRegime::Halt;
            return self.reject(VetoReason::IsaShortSellBlocked, ts);
        }

        // CHECK 6: Max Positions — filled + pending >= max (H34)
        // BUG-001 FIX: Was using !self.simulation_mode which bypassed position limits
        // in paper mode. Now uses enforce_live_gates so paper respects live limits
        // when paper_uses_live_gates=true (the default). This ensures paper data
        // is structurally transferable to live.
        if enforce_live_gates {
            let max_positions = match self.regime {
                RiskRegime::Reduce => (self.config.max_positions / 2).max(1),
                RiskRegime::Flatten | RiskRegime::Halt => 0,
                RiskRegime::Normal => self.config.max_positions,
            };
            if portfolio.total_position_count() >= max_positions {
                return self.reject(VetoReason::MaxPositionsReached, ts);
            }
        }

        // CHECK 2: Inverse Mutual Exclusion (H32) — requires portfolio lookup
        if let Some(blocker) = portfolio.inverse_blocker(intent_ticker) {
            return self.reject(
                VetoReason::InverseMutualExclusion { blocker: blocker.0 },
                ts,
            );
        }

        // CHECK 10: Confidence Floor — FIXED (Sprint 5, T-07): leverage-aware.
        // 3x ETP with base floor 55% → adjusted floor 55/√3 = 31.8%
        // 5x ETP with base floor 55% → adjusted floor 55/√5 = 24.6%
        // 1x equity with base floor 55% → floor 55% (unchanged)
        // Rationale: leveraged products amplify moves, so a lower raw confidence
        // still represents a high expected-value trade after leverage.
        // P3.3 item 12: Regime-scaled — Reduce raises floor by 15%, Flatten rejects all.
        let leverage_sqrt = (ctx.leverage_factor.max(1) as f64).sqrt();
        let base_floor = match self.regime {
            RiskRegime::Reduce => self.config.confidence_floor * 1.15,
            RiskRegime::Flatten | RiskRegime::Halt => {
                // Flatten/Halt: reject all entries regardless of confidence.
                return self.reject(
                    VetoReason::ConfidenceBelowFloor {
                        confidence_x10: (intent_confidence * 10.0) as u32,
                    },
                    ts,
                );
            }
            RiskRegime::Normal => self.config.confidence_floor,
        };
        let adjusted_floor = base_floor / leverage_sqrt;
        if intent_confidence < adjusted_floor {
            return self.reject(
                VetoReason::ConfidenceBelowFloor {
                    confidence_x10: (intent_confidence * 10.0) as u32,
                },
                ts,
            );
        }

        // CHECK 11: Time-of-Day Cutoff — per-exchange or global (H35)
        // Sprint 7: Use per-exchange cutoff if configured, else fall back to global.
        // In simulation mode, skip time cutoff to collect data across all trading hours.
        if enforce_live_gates {
            let effective_cutoff = self.exchange_cutoffs_secs
                .get(&ctx.exchange_mic)
                .copied()
                .unwrap_or(self.config.entry_cutoff_secs);
            if ctx.time_secs >= effective_cutoff {
                return self.reject(VetoReason::TooLateInSession, ts);
            }
        }

        // CHECK 12: REMOVED — Auction period blocking was LSE-specific (07:50-08:00, 16:30-16:35).
        // Global engine trades 6 markets across all timezones. Spread veto (CHECK 13)
        // provides natural protection during auction periods (spreads widen → rejected).

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

        // CHECK 28: Daily Trade Limit (N0a) — THE #1 cost control gate.
        // At 0.50% RT cost per trade, every trade costs ~£10 on £2K position.
        // 3 trades/day × 252 = £7,560/year = 76% equity drag on £10K.
        // ALWAYS enforced, including simulation mode, so paper data reflects live economics.
        // P3.3 item 10: Regime-scaled — Reduce halves daily budget, Flatten blocks all.
        let daily_limit = match self.regime {
            RiskRegime::Reduce => (self.config.daily_trade_limit / 2).max(1),
            RiskRegime::Flatten | RiskRegime::Halt => 0,
            RiskRegime::Normal => self.config.daily_trade_limit,
        };
        if portfolio.daily_trade_count >= daily_limit {
            return self.reject(
                VetoReason::DailyTradeLimitReached {
                    count: portfolio.daily_trade_count,
                    limit: daily_limit,
                },
                ts,
            );
        }

        // CHECK 29: Minimum Gross Edge (N0d) — reject low-edge trades.
        // If spread is available, require Kelly signal to imply edge > min_gross_edge_pct.
        // Uses kelly_fraction as a proxy for expected edge (higher Kelly = higher edge).
        // Minimum: kelly_fraction > 0.01 AND spread doesn't consume >50% of expected return.
        if ctx.bid > 0.0 && self.config.min_gross_edge_pct > 0.0 {
            let spread_pct = (ctx.ask - ctx.bid) / ctx.bid * 100.0;
            // Reject if spread alone exceeds the minimum edge threshold
            if spread_pct > self.config.min_gross_edge_pct * self.config.spread_edge_ratio {
                return self.reject(
                    VetoReason::GrossEdgeTooLow {
                        edge_bps: (self.config.min_gross_edge_pct * 100.0) as u32,
                        min_bps: (spread_pct * 100.0) as u32,
                    },
                    ts,
                );
            }
        }

        // CHECK 14: Cash Buffer (H31)
        // In simulation mode, skip cash buffer to allow maximum data collection.
        // With £10K equity and 20 max positions, trades fill up cash fast.
        if enforce_live_gates && portfolio.cash_buffer_pct() < self.config.cash_buffer_pct {
            return self.reject(VetoReason::CashBufferInsufficient, ts);
        }

        // CHECK 15: Portfolio Heat
        // In simulation mode, skip portfolio heat cap for broad evidence gathering.
        if enforce_live_gates && portfolio.portfolio_heat_pct() >= self.config.portfolio_heat_limit_pct {
            return self.reject(VetoReason::PortfolioHeatExceeded, ts);
        }

        // CHECK 16: Sector Heat (H30)
        // In simulation mode, skip sector heat to allow maximum data collection across all tickers.
        if enforce_live_gates
            && portfolio.sector_heat_pct(intent_ticker) >= self.config.sector_heat_cap_pct
        {
            let heat = portfolio.sector_heat_pct(intent_ticker);
            let sector = sector_for_ticker(intent_ticker);
            return self.reject(
                VetoReason::SectorHeatExceeded {
                    sector: format!("{sector:?}"),
                    pct: heat as u32,
                },
                ts,
            );
        }

        // CHECK 17: ISA Annual Limit
        // In simulation mode, skip ISA limit to allow unlimited data collection.
        // Per UNLIMITED SIMULATION BUDGET directive: sim needs maximum trade variety.
        if enforce_live_gates && portfolio.isa_year_invested >= self.config.isa_annual_limit_gbp {
            return self.reject(VetoReason::IsaAnnualLimitExceeded, ts);
        }

        // CHECK 18: Daily Drawdown — >2% from high-water → FLATTEN (H29)
        // In simulation mode, skip drawdown circuit breaker. Sim uses infinite budget;
        // FLATTEN would halt all data collection for the rest of the session.
        if enforce_live_gates && portfolio.daily_drawdown_pct() > self.config.daily_drawdown_pct {
            self.regime = RiskRegime::Flatten;
            return self.reject(VetoReason::DailyDrawdownBreached, ts);
        }

        // CHECK 30: Weekly Drawdown — configurable % from Monday HWM → FLATTEN (Sprint 10)
        if enforce_live_gates && portfolio.weekly_drawdown_pct() > self.config.weekly_drawdown_pct {
            self.regime = RiskRegime::Flatten;
            return self.reject(VetoReason::WeeklyDrawdownBreached, ts);
        }

        // CHECK 31: Peak Drawdown from all-time HWM → HALT (Sprint 10)
        if enforce_live_gates {
            let peak_dd = portfolio.peak_drawdown_pct();
            if peak_dd > self.config.peak_drawdown_halt_pct {
                self.regime = RiskRegime::Halt;
                return self.reject(VetoReason::PeakDrawdownHalt { drawdown_pct: peak_dd as u32 }, ts);
            }
        }

        // CHECK 32: Equity Floor — hard floor at % of initial equity (Sprint 10)
        if enforce_live_gates {
            let floor = portfolio.initial_equity * self.config.equity_floor_pct / 100.0;
            if portfolio.equity < floor {
                self.regime = RiskRegime::Halt;
                return self.reject(VetoReason::EquityFloorBreached, ts);
            }
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

        // CHECK 19b: System-wide velocity — max system_velocity_max entries per 5-minute window across ALL tickers.
        let system_cutoff = ts.saturating_sub(VELOCITY_WINDOW_5MIN_NS);
        let system_recent = self.velocity_log.iter().filter(|(_, t)| *t >= system_cutoff).count();
        if system_recent >= self.config.system_velocity_max {
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

        // CHECK 22: Duplicate Position — gated by momentum re-entry
        if ctx.ticker_position_count > 0 {
            let max_allowed = if ctx.ticker_locked {
                1 // Locked tickers: no re-entry
            } else if ctx.ticker_ic >= self.config.reentry_3pos_ic
                && ctx.ticker_trade_count >= self.config.reentry_3pos_trades
            {
                3
            } else if ctx.ticker_ic >= self.config.reentry_2pos_ic
                && ctx.ticker_trade_count >= self.config.reentry_2pos_trades
            {
                2
            } else {
                1 // Default: single position only
            };
            if ctx.ticker_position_count >= max_allowed {
                return self.reject(VetoReason::DuplicatePosition, ts);
            }
        }

        // CHECK 23: Ticker Halted (reverse split, synthetic halt, etc)
        if ctx.ticker_halted {
            return self.reject(VetoReason::TickerHalted, ts);
        }

        // CHECK 24: CVaR Heat — portfolio-level conditional value at risk
        let cvar_heat = portfolio.cvar_heat_pct(&ctx.volatilities);
        if cvar_heat > self.config.portfolio_heat_limit_pct * self.config.cvar_heat_multiplier {
            return self.reject(
                VetoReason::CvarHeatExceeded {
                    cvar_pct: cvar_heat as u32,
                },
                ts,
            );
        }

        // CHECK 25: GARCH forecast sigma too high — leverage-scaled (Avellaneda & Zhang 2010).
        // P0-03 FIX: 3x ETP has ~√3 × vol, 5x has ~√5 × vol. Scale threshold accordingly.
        let garch_threshold = self.config.garch_threshold_base * (ctx.leverage_factor.max(1) as f64).sqrt();
        if ctx.garch_sigma > garch_threshold {
            return self.reject(
                VetoReason::GarchVolTooHigh {
                    sigma_pct: (ctx.garch_sigma * 100.0) as u32,
                },
                ts,
            );
        }

        // CHECK 26: Scanner score below minimum threshold (< 30)
        if ctx.scanner_score > 0.0 && ctx.scanner_score < self.config.scanner_score_min {
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
        if ctx.kelly_fraction_raw > 0.0 && ctx.kelly_fraction_raw < self.config.kelly_fraction_floor {
            return self.reject(VetoReason::KellyBelowFloor, ts);
        }

        // CHECK 34: Correlation — max positions in >0.7 correlated instruments (Book 41, Phase 8).
        // Uses the volatilities map as a proxy: tickers with similar vol profiles are correlated.
        // Full Hayashi-Yoshida correlation will be wired when H-Y output is consumed (Phase 8+).
        if enforce_live_gates && self.config.max_correlated_positions > 0 {
            let sector = crate::sector_rotation::sector_for_ticker(intent_ticker);
            let sector_name = format!("{sector:?}");
            let same_sector_count = portfolio.count_positions_in_sector(&sector_name);
            if same_sector_count >= self.config.max_correlated_positions {
                return self.reject(VetoReason::CorrelationConcentration {
                    corr_pct: (same_sector_count * 100 / self.config.max_correlated_positions.max(1)) as u32,
                }, ts);
            }
        }

        // CHECK 35: Structural Tradability Score — reject untradeable instruments (Book 43).
        // Score is computed by Python bridge from spread, volume, and liquidity metrics.
        // < 15 = completely untradeable; default 50.0 (neutral) in EvalContext.
        if ctx.structural_score < 15.0 {
            return self.reject(VetoReason::StructuralScoreTooLow {
                score: ctx.structural_score as u32,
            }, ts);
        }

        // CHECK 40: EVT CVaR Tail Risk — reject when expected shortfall is extreme.
        // evt_cvar is the conditional value-at-risk from Extreme Value Theory (GARCH-EVT).
        // > 0.15 (15% expected loss in tail) on a 3x ETP means potential -45% drawdown.
        // Scale threshold by leverage: 0.15 for 1x, 0.05 for 3x, 0.03 for 5x.
        if ctx.evt_cvar > 0.0 {
            let cvar_threshold = 0.15 / (ctx.leverage_factor.max(1) as f64);
            if ctx.evt_cvar > cvar_threshold {
                return self.reject(VetoReason::CvarHeatExceeded {
                    cvar_pct: (ctx.evt_cvar * 100.0) as u32,
                }, ts);
            }
        }

        // CHECK 41: Kalman Divergence — reject when price diverges >3% from smoothed.
        // Positive divergence = raw price leads (potential overextension/reversal risk).
        // Negative divergence = raw price lags (potential gap-fill risk).
        // Only veto extreme divergence that signals unreliable pricing.
        if ctx.kalman_divergence.abs() > 0.03 {
            return self.reject(VetoReason::GapDetected {
                gap_bps: (ctx.kalman_divergence.abs() * 10_000.0) as u32,
            }, ts);
        }

        // CHECK 42: Native Spread Quality — tighten size when spread is wide in native currency.
        // native_spread_bps pre-FX gives true execution cost. > 50bps = thin market, reduce size.
        // This is additive to CHECK 13 (spread veto) which uses GBP-converted spread.
        // Not a veto — just sizing adjustment applied in final sizing below.

        // CHECK 36: Session Exposure Limits (Book 7)
        // Max NAV by session: Asia 30%, Europe 50%, US 60%, EU+US Overlap 80%.
        if enforce_live_gates {
            if let Some(veto) = self.check_session_exposure(intent_ticker, portfolio, ctx) {
                return self.reject(veto, ts);
            }
        }

        // CHECK 37: Regime-Scaled Daily Loss Limit (Book 85)
        // Dynamic daily circuit breaker based on regime: STEADY -3%, INFLATION -2.5%, WOI -2%, CRISIS -1.5%
        if enforce_live_gates {
            if let Some(veto) = self.check_regime_daily_loss(portfolio) {
                return self.reject(veto, ts);
            }
        }

        // CHECK 38: Regime-Scaled Weekly Loss Limit (Book 85)
        // Dynamic weekly circuit breaker based on regime: STEADY -7%, INFLATION -5.5%, WOI -4%, CRISIS -2%
        if enforce_live_gates {
            if let Some(veto) = self.check_regime_weekly_loss(portfolio) {
                return self.reject(veto, ts);
            }
        }

        // CHECK 39: Regime-Scaled Risk Per Trade (Book 85)
        // Cap position size to regime-appropriate risk budget: STEADY 0.75%, INFLATION 0.60%, WOI 0.40%, CRISIS 0.20%
        // Applied during position sizing below (after effective_kelly calculation).

        // All checks passed. Calculate adjusted size.
        // SC-13: Kelly scaling ramp — configurable target, clamp range
        let kelly_ramp = (self.config.kelly_ramp_trades as f64 / self.config.kelly_ramp_target as f64)
            .clamp(self.config.kelly_ramp_clamp_min, self.config.kelly_ramp_clamp_max);
        let ramped_kelly = effective_kelly * kelly_ramp;

        // Ouroboros-calibrated regime scaling (fall back to hardcoded defaults).
        let regime_name = format!("{:?}", self.regime);
        let default_scale = if self.regime == RiskRegime::Reduce { 0.5 } else { 1.0 };
        let regime_scale = self.regime_scales.get(&regime_name).copied().unwrap_or(default_scale);

        // ── SCORE-TO-KELLY TRANSLATOR ────────────────────────────────────
        // Dynamic Universe final_score weights allocation: higher-scored symbols
        // get proportionally more capital. Half-Kelly default (0.5 safety factor).
        // Score range: 0.0-1.5 → multiplier 0.5-1.5 (linear, clamped).
        let ticker_symbol = ctx.exchange_mic.clone(); // Use symbol from context if available
        let score_raw = self.rotation_scores
            .get(&ticker_key)
            .or_else(|| self.rotation_scores.get(&ticker_symbol))
            .copied()
            .unwrap_or(0.5); // Unscored symbols get neutral 0.5
        // Normalize: score 0.5 = 1.0x, score 1.0 = 1.5x, score 0.0 = 0.5x
        let score_multiplier = (0.5 + score_raw).clamp(0.5, 1.5);

        // ── EQUITY RATCHET (HWM Drawdown Brake) ─────────────────────────
        // Track all-time HWM. Scale Kelly down on drawdowns to prevent ruin.
        // equity >= 95% HWM → 1.0x | >= 90% → 0.75x | >= 80% → 0.50x | < 80% → 0.25x
        if portfolio.equity > self.equity_hwm {
            self.equity_hwm = portfolio.equity;
        }
        let ratchet_multiplier = if self.equity_hwm <= 0.0 {
            1.0
        } else {
            let ratio = portfolio.equity / self.equity_hwm;
            if ratio >= 0.95 { 1.00 }
            else if ratio >= 0.90 { 0.75 }
            else if ratio >= 0.80 { 0.50 }
            else { 0.25 }
        };

        // ── VOLATILITY PARITY (ATR Normalization) ────────────────────────
        // Divide allocated capital by ATR to equalize risk contribution per position.
        // Higher ATR → fewer shares → same dollar risk. Uses 14-period Wilder ATR.
        // ATR factor: normalize ATR to a target risk per position (2% of entry).
        // If ATR data unavailable, factor = 1.0 (no adjustment).
        let atr_factor = if ctx.garch_sigma > 0.0 {
            // garch_sigma is annualized; convert to daily risk proxy
            let daily_vol = ctx.garch_sigma / 252.0_f64.sqrt();
            // Target: 2% daily risk. If actual is higher, reduce size proportionally.
            let target_daily_risk = 0.02;
            if daily_vol > target_daily_risk {
                target_daily_risk / daily_vol
            } else {
                1.0 // Low vol = no reduction (let Kelly handle upside)
            }
        } else {
            1.0 // No vol data = no adjustment
        };

        // ── NATIVE SPREAD QUALITY SCALING (CHECK 42) ─────────────────────
        // Wide native spreads = thin market = reduce size to limit slippage.
        // > 50bps → 0.75x, > 100bps → 0.50x, > 200bps → 0.25x
        let spread_quality_factor = if ctx.native_spread_bps > 200.0 {
            0.25
        } else if ctx.native_spread_bps > 100.0 {
            0.50
        } else if ctx.native_spread_bps > 50.0 {
            0.75
        } else {
            1.0
        };

        // ── FINAL SIZING ─────────────────────────────────────────────────
        let base_size = ramped_kelly * portfolio.equity;
        let adjusted_size = base_size * regime_scale * score_multiplier * ratchet_multiplier * atr_factor * spread_quality_factor;
        // Multi-constraint sizing: cap by equity percentage to prevent oversized positions.
        let max_by_equity = portfolio.equity * self.config.max_entry_pct_of_equity;
        let adjusted_size = adjusted_size.min(max_by_equity);

        // Sizing instrumentation: log every candidate that passes all checks
        eprintln!(
            "SIZING: ticker={} kelly_in={:.4} effective={:.4} ramp={:.2} ramped={:.4} \
             equity={:.0} base={:.0} regime={:.2} score={:.2}(raw={:.2}) ratchet={:.2} \
             atr_factor={:.2} spread_q={:.2} adjusted={:.0} min_entry={:.0} conf={:.1}",
            intent_ticker.0, intent_kelly, effective_kelly, kelly_ramp,
            ramped_kelly, portfolio.equity, base_size, regime_scale,
            score_multiplier, score_raw, ratchet_multiplier,
            atr_factor, spread_quality_factor, adjusted_size,
            self.config.minimum_entry_gbp, intent_confidence,
        );

        // SC-05: Minimum entry size gate.
        // Suspended during Kelly ramp when validated_trades < KELLY_RAMP_TARGET.
        if self.config.kelly_ramp_trades >= self.config.kelly_ramp_target && adjusted_size < self.config.minimum_entry_gbp {
            return self.reject(
                VetoReason::BelowMinimumEntrySize {
                    size_gbp: adjusted_size as u32,
                },
                ts,
            );
        }

        // Record approved intent for velocity tracking
        self.velocity_log.push_back((intent_ticker, ts));

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

    #[cold]
    #[inline(never)]
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
        // O(1) amortized: pop expired entries from front (VecDeque is time-ordered).
        while let Some(&(_, ts)) = self.velocity_log.front() {
            if now_ns.saturating_sub(ts) > 300_000_000_000 {
                self.velocity_log.pop_front();
            } else {
                break;
            }
        }
        // Hard ceiling: prevent unbounded growth from clock skew or bursts.
        if self.velocity_log.len() > 50_000 {
            self.velocity_log.pop_front();
        }
    }

    /// CHECK 36: Session Exposure Limits (Book 7).
    /// Prevents overconcentration by session:
    /// - Asia: 30% of equity
    /// - Europe: 50% of equity
    /// - US: 60% of equity
    /// - EU+US Overlap (14:30-15:30 London): 80% of equity
    ///
    /// Returns VetoReason if exposure would exceed limit, None otherwise.
    fn check_session_exposure(
        &self,
        intent_ticker: TickerId,
        portfolio: &PortfolioState,
        ctx: &EvalContext,
    ) -> Option<VetoReason> {
        if ctx.exchange_mic.is_empty() || portfolio.equity <= 0.0 {
            // Unknown exchange or no equity: skip check (safe default)
            return None;
        }

        // Determine current trading session from London time
        let current_session = TradingSession::from_london_secs(ctx.time_secs);

        // Classify the intent ticker's exchange to a session
        let intent_exchange_session = TradingSession::classify_exchange(&ctx.exchange_mic);

        // Determine applicable exposure limit based on current time and intent exchange
        let (session_name, limit_pct) = match current_session {
            TradingSession::PostMarket => {
                // After 21:00 London: no new entries allowed anyway (CHECK 11), skip session check
                return None;
            }
            TradingSession::Asia => {
                // 00:00-08:00: Only Asia sessions are open
                match intent_exchange_session {
                    TradingSession::Asia => ("Asia", SESSION_ASIA_LIMIT_PCT),
                    _ => {
                        // Attempting to trade non-Asia exchange during Asia hours
                        // Use the more restrictive limit for the intent exchange
                        (intent_exchange_session.name(), SESSION_ASIA_LIMIT_PCT)
                    }
                }
            }
            TradingSession::Europe => {
                // 08:00-14:30: Europe session (US not yet open)
                match intent_exchange_session {
                    TradingSession::Europe => ("Europe", SESSION_EUROPE_LIMIT_PCT),
                    TradingSession::Asia => ("Europe", SESSION_EUROPE_LIMIT_PCT),
                    _ => ("Europe", SESSION_EUROPE_LIMIT_PCT),
                }
            }
            TradingSession::Us => {
                // 14:30-21:00: US session
                // 14:30-15:30 (52200-56400): Europe+US overlap → 80% limit
                // 15:30-21:00 (56400-75600): US core hours → 60% limit
                let is_eu_us_overlap = ctx.time_secs >= 52200 && ctx.time_secs < 56400; // 14:30-15:30
                if is_eu_us_overlap {
                    ("US+EU_Overlap", SESSION_OVERLAP_EU_US_LIMIT_PCT)
                } else {
                    ("US", SESSION_US_LIMIT_PCT)
                }
            }
            TradingSession::Overlap => {
                // This variant shouldn't be reached (handled by Us), but for completeness:
                ("US+EU_Overlap", SESSION_OVERLAP_EU_US_LIMIT_PCT)
            }
        };

        // Calculate current session exposure (% of equity)
        let mut session_notional = 0.0;

        for (ticker, position) in portfolio.positions() {
            // Skip the intent ticker (not yet added to portfolio)
            if *ticker == intent_ticker {
                continue;
            }

            // For simplicity, use average entry price × quantity as notional
            // (Could enhance with current mark-to-market for precision)
            let position_notional = position.avg_entry * position.qty as f64;
            session_notional += position_notional;
        }

        let current_exposure_pct = (session_notional / portfolio.equity) * 100.0;

        // Check if adding this position would exceed the limit
        if current_exposure_pct >= limit_pct {
            return Some(VetoReason::SessionExposureExceeded {
                session: session_name.to_string(),
                exposure_pct: current_exposure_pct as u32,
                limit_pct: limit_pct as u32,
            });
        }

        None
    }

    /// CHECK 20: Macro Regime Escalation (Phase 9).
    /// Evaluates macro indicators (VIX, DXY, credit spreads, Fear & Greed) and escalates regime if needed.
    /// P1-2.7: VIX hysteresis deadband (enter HIGH_VIX at 25, exit at 22; enter EXTREME at 35, exit at 30).
    /// Returns VetoReason if regime escalation triggered, None otherwise.
    fn evaluate_macro_escalation(&mut self, ctx: &EvalContext) -> Option<VetoReason> {
        let macro_eval = CrossAssetMacro::from_indicator(ctx.macro_indicator);
        let macro_signal = macro_eval.evaluate();
        let vix = ctx.macro_indicator.vix;

        // P1-2.7: VIX hysteresis deadband — prevents flip-flopping at VIX boundaries.
        // Enter high: VIX >= VIX_HIGH_ENTER, exit high: VIX < VIX_HIGH_EXIT (3-point deadband)
        // Enter extreme: VIX >= VIX_EXTREME_ENTER, exit extreme: VIX < VIX_EXTREME_EXIT (5-point deadband)
        if vix >= self.config.vix_extreme_enter {
            self.vix_extreme = true;
            self.vix_high = true;
        } else if vix < self.config.vix_extreme_exit {
            self.vix_extreme = false;
        }
        if vix >= self.config.vix_high_enter {
            self.vix_high = true;
        } else if vix < self.config.vix_high_exit {
            self.vix_high = false;
        }

        // Trigger A: VIX Crisis → FLATTEN (allow exits, block new entries)
        if macro_signal == MacroRegimeSignal::Crisis || self.vix_extreme {
            self.regime = RiskRegime::Flatten;
            return Some(VetoReason::MacroCrisisDetected {
                vix: (vix * 10.0) as u32,
                credit_bps: ctx.macro_indicator.credit_spread_bps as u32,
            });
        }

        // VIX high (with hysteresis) → REDUCE
        if self.vix_high && self.regime < RiskRegime::Reduce {
            self.regime = RiskRegime::Reduce;
            // Don't return veto — REDUCE allows entries at 0.5x size
        }

        // Trigger B: Macro Stress + Stale Ticks → HALT (data unreliable)
        if macro_signal == MacroRegimeSignal::Stress && ctx.last_tick_age_secs > self.config.macro_stress_stale_tick_secs {
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

    /// P1-2.16: Record equity snapshot for drawdown velocity tracking.
    /// Call this every tick/evaluation cycle with current equity.
    pub fn record_equity_snapshot(&mut self, now_ns: u64, equity: f64) {
        let interval_ns: u64 = self.config.equity_snapshot_interval_secs * 1_000_000_000;
        if let Some((last_ts, _)) = self.equity_snapshots.last() {
            if now_ns < *last_ts + interval_ns {
                return;
            }
        }
        self.equity_snapshots.push((now_ns, equity));
        let cutoff = now_ns.saturating_sub(self.config.equity_snapshot_retention_secs * 1_000_000_000);
        self.equity_snapshots.retain(|(ts, _)| *ts >= cutoff);
    }

    /// P1-2.16: Check drawdown velocity — if equity dropped >2% in 1 hour → HALT.
    pub fn check_drawdown_velocity(&mut self, now_ns: u64, current_equity: f64) -> bool {
        let window_ns = self.config.drawdown_velocity_window_secs * 1_000_000_000;
        let window_ago = now_ns.saturating_sub(window_ns);
        if let Some((_, equity_then)) = self.equity_snapshots.iter().find(|(ts, _)| *ts >= window_ago) {
            if *equity_then > 0.0 {
                let drawdown_pct = ((*equity_then - current_equity) / *equity_then) * 100.0;
                if drawdown_pct > self.config.drawdown_velocity_pct {
                    eprintln!(
                        "DRAWDOWN_VELOCITY: {:.2}% drawdown in 1 hour (threshold 2%) — escalating to HALT",
                        drawdown_pct
                    );
                    self.regime = RiskRegime::Halt;
                    return true;
                }
            }
        }
        false
    }

    /// CHECK 37: Regime-Scaled Daily Loss Limit (Book 85).
    /// Dynamic daily drawdown circuit breaker scaled by current regime.
    /// Thresholds:
    /// - STEADY: -3.0%
    /// - INFLATION: -2.5%
    /// - WOI: -2.0%
    /// - CRISIS: -1.5%
    ///
    /// Returns VetoReason if limit breached (initiates FLATTEN), None otherwise.
    fn check_regime_daily_loss(&self, portfolio: &PortfolioState) -> Option<VetoReason> {
        // Regime-scaled limits (more restrictive in crisis)
        let daily_loss_limit = match self.regime {
            RiskRegime::Halt => return None,     // Already halted, skip check
            RiskRegime::Flatten => return None,  // Already flattening, skip check
            RiskRegime::Reduce => -2.5,          // Reduce uses INFLATION limit
            RiskRegime::Normal => -3.0,          // Normal uses STEADY limit
        };

        let daily_dd = portfolio.daily_drawdown_pct();
        if daily_dd < daily_loss_limit {
            // Exceeded limit (more negative than threshold)
            eprintln!(
                "CHECK_37 REGIME_DAILY_LOSS: drawdown {:.2}% exceeds regime limit {:.2}% (regime={:?}) → FLATTEN",
                daily_dd, daily_loss_limit, self.regime
            );
            return Some(VetoReason::RegimeDailyLossLimitBreached {
                drawdown_pct: (daily_dd * 100.0) as u32,
                limit_pct: (daily_loss_limit * 100.0) as i32,
            });
        }
        None
    }

    /// CHECK 38: Regime-Scaled Weekly Loss Limit (Book 85).
    /// Dynamic weekly drawdown circuit breaker scaled by current regime.
    /// Thresholds:
    /// - STEADY: -7.0%
    /// - INFLATION: -5.5%
    /// - WOI: -4.0%
    /// - CRISIS: -2.0%
    ///
    /// Returns VetoReason if limit breached (initiates FLATTEN), None otherwise.
    fn check_regime_weekly_loss(&self, portfolio: &PortfolioState) -> Option<VetoReason> {
        // Regime-scaled limits (more restrictive in crisis)
        let weekly_loss_limit = match self.regime {
            RiskRegime::Halt => return None,     // Already halted, skip check
            RiskRegime::Flatten => return None,  // Already flattening, skip check
            RiskRegime::Reduce => -5.5,          // Reduce uses INFLATION limit
            RiskRegime::Normal => -7.0,          // Normal uses STEADY limit
        };

        let weekly_dd = portfolio.weekly_drawdown_pct();
        if weekly_dd < weekly_loss_limit {
            // Exceeded limit (more negative than threshold)
            eprintln!(
                "CHECK_38 REGIME_WEEKLY_LOSS: drawdown {:.2}% exceeds regime limit {:.2}% (regime={:?}) → FLATTEN",
                weekly_dd, weekly_loss_limit, self.regime
            );
            return Some(VetoReason::RegimeWeeklyLossLimitBreached {
                drawdown_pct: (weekly_dd * 100.0) as u32,
                limit_pct: (weekly_loss_limit * 100.0) as i32,
            });
        }
        None
    }

    /// CHECK 39: Regime-Scaled Risk Per Trade (Book 85).
    /// Applied during position sizing to cap risk per trade by regime.
    /// Thresholds (% of equity):
    /// - STEADY: 0.75%
    /// - INFLATION: 0.60%
    /// - WOI: 0.40%
    /// - CRISIS: 0.20%
    ///
    /// Called by evaluate() after effective_kelly is calculated.
    /// Returns scaled Kelly cap as multiplier [0.0, 1.0].
    pub fn regime_risk_per_trade_scale(&self) -> f64 {
        match self.regime {
            RiskRegime::Halt => 0.0,           // No trades in HALT
            RiskRegime::Flatten => 0.0,        // No new entries in FLATTEN
            RiskRegime::Reduce => 0.40 / 0.75, // WOI/INFLATION scale relative to STEADY
            RiskRegime::Normal => 1.0,         // STEADY = full 0.75% allowed
        }
    }
}

