//! Risk configuration constants extracted from config/config.toml (H109).
//! Code MUST reference these, not literal values.

/// All configurable risk parameters.
#[derive(Clone, Debug)]
pub struct RiskConfig {
    /// Max filled + pending positions (H34)
    pub max_positions: u32,
    /// Sum of position risk / equity cap (%)
    pub portfolio_heat_limit_pct: f64,
    /// No single sector > this % of equity (H30)
    pub sector_heat_cap_pct: f64,
    /// Available cash must be > this % of equity (H31)
    pub cash_buffer_pct: f64,
    /// Daily loss from high-water → FLATTEN (H29)
    pub daily_drawdown_pct: f64,
    /// Real-time spread > this → REJECT (H36)
    pub spread_veto_pct: f64,
    /// IBKR timestamp age → HALT
    pub stale_data_threshold_secs: u64,
    /// Signal confidence floor
    pub confidence_floor: f64,
    /// Seconds from midnight London: no entries after this
    pub entry_cutoff_secs: u32,
    /// Auction period boundaries (seconds from midnight London)
    pub auction_open_start_secs: u32,
    pub auction_open_end_secs: u32,
    pub auction_close_start_secs: u32,
    pub auction_close_end_secs: u32,
    /// Velocity burst window
    pub velocity_window_ns: u64,
    /// Velocity max identical intents before rejection
    pub velocity_max_intents: u32,
    /// Stop-losses in one day → HALT (H38)
    pub consecutive_loss_halt: u32,
    /// ISA annual investment cap
    pub isa_annual_limit_gbp: f64,
    /// SC-05: Minimum entry size in GBP. Below this → reject.
    pub minimum_entry_gbp: f64,
    /// SC-13: Number of validated trades for Kelly ramp calculation.
    pub kelly_ramp_trades: u32,
    /// N0a: Maximum trades per trading day. THE #1 survival lever.
    /// At 0.50% RT cost per trade, 3/day = 76% annual equity drag on £10K.
    pub daily_trade_limit: u32,
    /// N0d: Minimum gross edge (%) to justify entry after spread costs.
    /// Rejects trades where expected move < this threshold.
    pub min_gross_edge_pct: f64,

    // ── Sprint 6: Previously hardcoded constants, now configurable ──

    /// System-wide velocity: max entries per 5-minute window across ALL tickers.
    pub system_velocity_max: usize,
    /// Kelly ramp target: trades before full Kelly applied.
    pub kelly_ramp_target: u32,
    /// Kelly ramp clamp range.
    pub kelly_ramp_clamp_min: f64,
    pub kelly_ramp_clamp_max: f64,
    /// VIX hysteresis enter/exit thresholds.
    pub vix_high_enter: f64,
    pub vix_high_exit: f64,
    pub vix_extreme_enter: f64,
    pub vix_extreme_exit: f64,
    /// GARCH volatility ceiling (base, leverage-scaled via √leverage).
    pub garch_threshold_base: f64,
    /// CVaR heat multiplier (CVaR limit = Nx basic heat limit).
    pub cvar_heat_multiplier: f64,
    /// Momentum re-entry IC thresholds.
    pub reentry_3pos_ic: f64,
    pub reentry_3pos_trades: u32,
    pub reentry_2pos_ic: f64,
    pub reentry_2pos_trades: u32,
    /// Macro stress + stale tick threshold (seconds).
    pub macro_stress_stale_tick_secs: u64,
    /// Drawdown velocity: equity drop % in window → HALT.
    pub drawdown_velocity_pct: f64,
    pub drawdown_velocity_window_secs: u64,
    /// Equity snapshot recording interval and retention.
    pub equity_snapshot_interval_secs: u64,
    pub equity_snapshot_retention_secs: u64,
    /// Spread/edge rejection ratio.
    pub spread_edge_ratio: f64,
    /// Scanner score minimum threshold.
    pub scanner_score_min: f64,
    /// Kelly fraction floor.
    pub kelly_fraction_floor: f64,

    // ── Sprint 10: Portfolio risk gates ──

    /// Weekly drawdown limit (%) from Monday HWM → FLATTEN.
    pub weekly_drawdown_pct: f64,
    /// Peak drawdown from all-time HWM → full HALT.
    pub peak_drawdown_halt_pct: f64,
    /// Hard equity floor (% of initial equity) → HALT.
    pub equity_floor_pct: f64,
    /// Max overnight exposure as % of equity.
    pub overnight_exposure_cap_pct: f64,
    /// Max positions in >0.7 correlated instruments.
    pub max_correlated_positions: u32,
    /// Max risk per trade as % of equity.
    pub max_risk_per_trade_pct: f64,
    /// Max entry size as fraction of equity (e.g. 0.25 = 25%).
    pub max_entry_pct_of_equity: f64,

    // ── Book 7: Session Exposure Limits (was hardcoded in risk_arbiter.rs) ──

    /// Max NAV during Asia session (% of equity).
    pub session_asia_limit_pct: f64,
    /// Max NAV during Europe session (% of equity).
    pub session_europe_limit_pct: f64,
    /// Max NAV during US session (% of equity).
    pub session_us_limit_pct: f64,
    /// Max NAV during EU+US overlap (% of equity).
    pub session_overlap_limit_pct: f64,

    // ── Sprint 2: CVaR / Kalman / Spread Quality thresholds ──

    /// EVT CVaR tail risk base threshold (scaled by 1/leverage).
    pub cvar_threshold_base: f64,
    /// Kalman divergence absolute threshold for gap detection.
    pub kalman_divergence_threshold: f64,
    /// Native spread quality: thin market threshold (bps).
    pub spread_quality_thin_bps: f64,
    /// Native spread quality: wide market threshold (bps).
    pub spread_quality_wide_bps: f64,
    /// Native spread quality: extreme spread threshold (bps).
    pub spread_quality_extreme_bps: f64,

    // ── R6: Dividend withholding ──

    /// WP-6: Dividend withholding tax factor (UK ISA: 0.85 = 15% withholding).
    /// Moved from hardcoded 0.85 in PortfolioState to config.
    pub dividend_withholding_factor: f64,
}

impl Default for RiskConfig {
    fn default() -> Self {
        Self {
            max_positions: 6,
            portfolio_heat_limit_pct: 15.0,
            sector_heat_cap_pct: 33.0,
            cash_buffer_pct: 10.0,
            daily_drawdown_pct: 2.0,
            spread_veto_pct: 0.5,
            stale_data_threshold_secs: 120,
            confidence_floor: 65.0,
            entry_cutoff_secs: 20 * 3600 + 55 * 60, // 20:55 London (5 min before Dark at 21:00)
            auction_open_start_secs: 7 * 3600 + 50 * 60, // 07:50
            auction_open_end_secs: 8 * 3600,        // 08:00
            auction_close_start_secs: 16 * 3600 + 30 * 60, // 16:30
            auction_close_end_secs: 16 * 3600 + 35 * 60, // 16:35
            velocity_window_ns: 1_000_000_000,      // 1 second
            velocity_max_intents: 5,
            consecutive_loss_halt: 3,
            isa_annual_limit_gbp: 20_000.0,
            minimum_entry_gbp: 1500.0,
            kelly_ramp_trades: 0,
            daily_trade_limit: 3,
            min_gross_edge_pct: 0.15,
            // Sprint 6 defaults (match previous hardcoded constants)
            system_velocity_max: 10,
            kelly_ramp_target: 250,
            kelly_ramp_clamp_min: 0.1,
            kelly_ramp_clamp_max: 1.0,
            vix_high_enter: 25.0,
            vix_high_exit: 22.0,
            vix_extreme_enter: 35.0,
            vix_extreme_exit: 30.0,
            garch_threshold_base: 0.80,
            cvar_heat_multiplier: 1.5,
            reentry_3pos_ic: 0.20,
            reentry_3pos_trades: 20,
            reentry_2pos_ic: 0.10,
            reentry_2pos_trades: 10,
            macro_stress_stale_tick_secs: 60,
            drawdown_velocity_pct: 2.0,
            drawdown_velocity_window_secs: 3600,
            equity_snapshot_interval_secs: 60,
            equity_snapshot_retention_secs: 7200,
            spread_edge_ratio: 2.0,
            scanner_score_min: 30.0,
            kelly_fraction_floor: 0.005,
            // Sprint 10 defaults
            weekly_drawdown_pct: 7.0,
            peak_drawdown_halt_pct: 15.0,
            equity_floor_pct: 70.0,
            overnight_exposure_cap_pct: 50.0,
            max_correlated_positions: 3,
            max_risk_per_trade_pct: 0.75,
            max_entry_pct_of_equity: 0.25,
            // Book 7: Session exposure limits
            session_asia_limit_pct: 30.0,
            session_europe_limit_pct: 50.0,
            session_us_limit_pct: 60.0,
            session_overlap_limit_pct: 80.0,
            // Sprint 2: CVaR / Kalman / Spread Quality thresholds
            cvar_threshold_base: 0.15,
            kalman_divergence_threshold: 0.03,
            spread_quality_thin_bps: 50.0,
            spread_quality_wide_bps: 100.0,
            spread_quality_extreme_bps: 200.0,
            // R6: UK ISA dividend withholding (net = gross * 0.85)
            dividend_withholding_factor: 0.85,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_values_match_config_toml() {
        let cfg = RiskConfig::default();
        assert_eq!(cfg.max_positions, 6);
        assert_eq!(cfg.portfolio_heat_limit_pct, 15.0);
        assert_eq!(cfg.sector_heat_cap_pct, 33.0);
        assert_eq!(cfg.cash_buffer_pct, 10.0);
        assert_eq!(cfg.daily_drawdown_pct, 2.0);
        assert_eq!(cfg.spread_veto_pct, 0.5);
        assert_eq!(cfg.stale_data_threshold_secs, 120);
        assert_eq!(cfg.confidence_floor, 65.0);
        assert_eq!(cfg.consecutive_loss_halt, 3);
        assert_eq!(cfg.isa_annual_limit_gbp, 20_000.0);
    }
}
