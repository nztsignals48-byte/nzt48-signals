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
