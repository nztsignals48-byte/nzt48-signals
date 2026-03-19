//! Strategy configuration loader. Parses config/strategies.toml into typed structs.
//!
//! Every strategy parameter is read from TOML — nothing is hardcoded. Ouroboros can
//! override values at runtime via dynamic_weights.toml, but the schema and defaults
//! live here.
//!
//! Usage:
//! ```ignore
//! let registry = load_strategies(&config_dir)?;
//! let gap = &registry.strategies.gap_fade;
//! if gap.enabled { /* ... */ }
//! ```

use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

use crate::config_loader::ConfigError;

// ============================================================================
// Adaptive Ranges (min/max bounds for Ouroboros parameter tuning)
// ============================================================================

/// A min/max range for a single parameter, deserialized from `[min, max]`.
#[derive(Debug, Clone, Deserialize)]
pub struct AdaptiveRange(pub f64, pub f64);

impl AdaptiveRange {
    /// Lower bound.
    pub fn min(&self) -> f64 {
        self.0
    }
    /// Upper bound.
    pub fn max(&self) -> f64 {
        self.1
    }
    /// Clamp a value to this range.
    pub fn clamp(&self, v: f64) -> f64 {
        v.clamp(self.0, self.1)
    }
}

impl Default for AdaptiveRange {
    fn default() -> Self {
        Self(0.0, 100.0)
    }
}

// ============================================================================
// S17: VWAP Dip Buy
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct VwapDipBuyConfig {
    pub enabled: bool,
    pub priority: u32,
    pub family: String,
    pub base_confidence: f64,

    // Entry conditions
    pub entry_vwap_sigma: f64,
    pub entry_scale_in_sigma: f64,
    pub entry_volume_filter: String,
    pub entry_vwap_slope_max: f64,

    // Exit conditions
    pub exit_target: String,
    pub exit_target_conservative: String,
    pub exit_stop_sigma: f64,
    pub exit_stop_atr_mult: f64,
    pub exit_time_stop_minutes: u32,
    pub exit_eod_flatten: bool,

    // Filters
    pub filter_adx_max: f64,
    pub filter_hurst_max: f64,
    pub filter_rvol_min: f64,
    pub filter_rvol_max: f64,
    pub filter_spread_max_bps: u32,
    pub filter_vix_max: f64,
    pub filter_broad_market_not_at_lows: bool,
    pub filter_no_news_catalyst: bool,

    // Session eligibility
    pub session_eligible: Vec<String>,
    pub session_blocked: Vec<String>,

    // Regime eligibility
    pub regime_eligible: Vec<String>,
    pub regime_blocked: Vec<String>,

    // Sizing
    pub sizing_mult: f64,
    pub sizing_scale_in: Vec<f64>,

    // Ticker eligibility
    #[serde(default)]
    pub ticker_whitelist: Vec<String>,
    #[serde(default)]
    pub ticker_preferred: Vec<String>,

    // Adaptive ranges
    #[serde(default)]
    pub adaptive_ranges: VwapDipBuyAdaptiveRanges,
}

impl Default for VwapDipBuyConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            priority: 2,
            family: "mean_reversion".to_string(),
            base_confidence: 70.0,
            entry_vwap_sigma: 2.0,
            entry_scale_in_sigma: 3.0,
            entry_volume_filter: "declining".to_string(),
            entry_vwap_slope_max: 0.01,
            exit_target: "vwap".to_string(),
            exit_target_conservative: "vwap_minus_1sigma".to_string(),
            exit_stop_sigma: 3.0,
            exit_stop_atr_mult: 2.0,
            exit_time_stop_minutes: 90,
            exit_eod_flatten: true,
            filter_adx_max: 25.0,
            filter_hurst_max: 0.50,
            filter_rvol_min: 0.0,
            filter_rvol_max: 3.0,
            filter_spread_max_bps: 15,
            filter_vix_max: 30.0,
            filter_broad_market_not_at_lows: true,
            filter_no_news_catalyst: true,
            session_eligible: vec!["10:30-14:30".to_string(), "14:30-16:00".to_string()],
            session_blocked: vec!["08:00-08:30".to_string(), "16:00-16:30".to_string()],
            regime_eligible: vec!["mean_reverting".to_string(), "random".to_string()],
            regime_blocked: vec!["trending".to_string()],
            sizing_mult: 1.0,
            sizing_scale_in: vec![0.6, 0.4],
            ticker_whitelist: Vec::new(),
            ticker_preferred: vec!["QQQ3.L".to_string(), "3LUS.L".to_string()],
            adaptive_ranges: VwapDipBuyAdaptiveRanges::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct VwapDipBuyAdaptiveRanges {
    pub entry_vwap_sigma: AdaptiveRange,
    pub exit_stop_sigma: AdaptiveRange,
    pub exit_time_stop_minutes: AdaptiveRange,
    pub filter_adx_max: AdaptiveRange,
    pub base_confidence: AdaptiveRange,
}

impl Default for VwapDipBuyAdaptiveRanges {
    fn default() -> Self {
        Self {
            entry_vwap_sigma: AdaptiveRange(1.5, 3.0),
            exit_stop_sigma: AdaptiveRange(2.5, 4.0),
            exit_time_stop_minutes: AdaptiveRange(60.0, 180.0),
            filter_adx_max: AdaptiveRange(20.0, 30.0),
            base_confidence: AdaptiveRange(55.0, 85.0),
        }
    }
}

// ============================================================================
// S18: Gap Fade
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct GapFadeConfig {
    pub enabled: bool,
    pub priority: u32,
    pub family: String,
    pub base_confidence: f64,

    // Entry conditions
    pub entry_min_gap_pct: f64,
    pub entry_max_gap_pct: f64,
    pub entry_direction: String,
    pub entry_delay_minutes: u32,

    // Exit conditions
    pub exit_target_fill_pct: f64,
    pub exit_stop_pct: f64,
    pub exit_time_stop_minutes: u32,
    pub exit_eod_flatten: bool,

    // Filters
    pub filter_rvol_5min_max: f64,
    pub filter_rvol_5min_veto: f64,
    pub filter_no_earnings: bool,
    pub filter_no_macro_release: bool,
    pub filter_spread_max_bps: u32,
    pub filter_vix_max: f64,

    // Session eligibility
    pub session_eligible: Vec<String>,
    pub session_blocked: Vec<String>,

    // Day-of-week confidence adjustments
    #[serde(default)]
    pub day_confidence_adj: HashMap<String, f64>,

    // Regime eligibility
    pub regime_eligible: Vec<String>,
    pub regime_blocked: Vec<String>,

    // Sizing
    pub sizing_mult: f64,

    // Ticker eligibility
    #[serde(default)]
    pub ticker_preferred: Vec<String>,

    // Adaptive ranges
    #[serde(default)]
    pub adaptive_ranges: GapFadeAdaptiveRanges,
}

impl Default for GapFadeConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            priority: 1,
            family: "mean_reversion".to_string(),
            base_confidence: 72.0,
            entry_min_gap_pct: 1.5,
            entry_max_gap_pct: 6.0,
            entry_direction: "fade".to_string(),
            entry_delay_minutes: 15,
            exit_target_fill_pct: 0.75,
            exit_stop_pct: 1.5,
            exit_time_stop_minutes: 120,
            exit_eod_flatten: true,
            filter_rvol_5min_max: 2.0,
            filter_rvol_5min_veto: 5.0,
            filter_no_earnings: true,
            filter_no_macro_release: true,
            filter_spread_max_bps: 20,
            filter_vix_max: 35.0,
            session_eligible: vec!["08:15-10:00".to_string()],
            session_blocked: vec!["08:00-08:15".to_string()],
            day_confidence_adj: {
                let mut m = HashMap::new();
                m.insert("Mon".to_string(), -5.0);
                m.insert("Tue".to_string(), 0.0);
                m.insert("Wed".to_string(), 0.0);
                m.insert("Thu".to_string(), 5.0);
                m.insert("Fri".to_string(), 5.0);
                m
            },
            regime_eligible: vec![
                "mean_reverting".to_string(),
                "random".to_string(),
                "trending".to_string(),
            ],
            regime_blocked: Vec::new(),
            sizing_mult: 0.8,
            ticker_preferred: vec![
                "QQQ3.L".to_string(),
                "3LUS.L".to_string(),
                "NVD3.L".to_string(),
            ],
            adaptive_ranges: GapFadeAdaptiveRanges::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct GapFadeAdaptiveRanges {
    pub entry_min_gap_pct: AdaptiveRange,
    pub exit_target_fill_pct: AdaptiveRange,
    pub exit_time_stop_minutes: AdaptiveRange,
    pub base_confidence: AdaptiveRange,
}

impl Default for GapFadeAdaptiveRanges {
    fn default() -> Self {
        Self {
            entry_min_gap_pct: AdaptiveRange(1.0, 3.0),
            exit_target_fill_pct: AdaptiveRange(0.50, 1.00),
            exit_time_stop_minutes: AdaptiveRange(60.0, 180.0),
            base_confidence: AdaptiveRange(60.0, 85.0),
        }
    }
}

// ============================================================================
// S19: RSI(2)/IBS Mean Reversion
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct RsiIbsConfig {
    pub enabled: bool,
    pub priority: u32,
    pub family: String,
    pub base_confidence: f64,

    // Entry conditions
    pub entry_rsi_period: u32,
    pub entry_rsi_threshold: f64,
    pub entry_rsi_threshold_3x: f64,
    pub entry_ibs_threshold: f64,
    pub entry_ibs_threshold_3x: f64,
    pub entry_above_sma200: bool,
    pub entry_max_above_sma200_pct: f64,
    pub entry_macro_filter: bool,
    pub entry_timing: String,

    // Exit conditions
    pub exit_rule: String,
    pub exit_rsi_above: f64,
    pub exit_stop_pct: f64,
    pub exit_max_hold_days: u32,

    // Filters
    pub filter_vix_above_100d_ma: bool,
    pub filter_no_earnings_week: bool,
    pub filter_spread_max_bps: u32,

    // Session eligibility
    pub session_eligible: Vec<String>,
    pub session_blocked: Vec<String>,

    // Regime eligibility
    pub regime_eligible: Vec<String>,
    pub regime_blocked: Vec<String>,

    // Sizing
    pub sizing_mult: f64,
    pub sizing_3x_penalty: f64,

    // Ticker eligibility
    #[serde(default)]
    pub ticker_whitelist: Vec<String>,

    // Adaptive ranges
    #[serde(default)]
    pub adaptive_ranges: RsiIbsAdaptiveRanges,
}

impl Default for RsiIbsConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            priority: 3,
            family: "mean_reversion".to_string(),
            base_confidence: 75.0,
            entry_rsi_period: 2,
            entry_rsi_threshold: 5.0,
            entry_rsi_threshold_3x: 2.5,
            entry_ibs_threshold: 0.20,
            entry_ibs_threshold_3x: 0.10,
            entry_above_sma200: true,
            entry_max_above_sma200_pct: 5.0,
            entry_macro_filter: true,
            entry_timing: "close".to_string(),
            exit_rule: "close_above_5sma".to_string(),
            exit_rsi_above: 50.0,
            exit_stop_pct: 5.0,
            exit_max_hold_days: 10,
            filter_vix_above_100d_ma: true,
            filter_no_earnings_week: true,
            filter_spread_max_bps: 20,
            session_eligible: vec!["20:30-21:00".to_string()],
            session_blocked: Vec::new(),
            regime_eligible: vec!["mean_reverting".to_string(), "random".to_string()],
            regime_blocked: vec!["trending".to_string()],
            sizing_mult: 0.6,
            sizing_3x_penalty: 0.5,
            ticker_whitelist: vec![
                "QQQ3.L".to_string(),
                "3LUS.L".to_string(),
                "QQQS.L".to_string(),
                "3USS.L".to_string(),
            ],
            adaptive_ranges: RsiIbsAdaptiveRanges::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct RsiIbsAdaptiveRanges {
    pub entry_rsi_threshold: AdaptiveRange,
    pub entry_ibs_threshold: AdaptiveRange,
    pub exit_stop_pct: AdaptiveRange,
    pub base_confidence: AdaptiveRange,
}

impl Default for RsiIbsAdaptiveRanges {
    fn default() -> Self {
        Self {
            entry_rsi_threshold: AdaptiveRange(2.0, 10.0),
            entry_ibs_threshold: AdaptiveRange(0.05, 0.30),
            exit_stop_pct: AdaptiveRange(3.0, 8.0),
            base_confidence: AdaptiveRange(65.0, 85.0),
        }
    }
}

// ============================================================================
// S20: Cross-Market Momentum
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct CrossMarketMomentumConfig {
    pub enabled: bool,
    pub priority: u32,
    pub family: String,
    pub base_confidence: f64,

    // Entry conditions
    pub entry_spy_direction_minutes: u32,
    pub entry_spy_min_move_pct: f64,
    pub entry_alignment: String,

    // Exit conditions
    pub exit_trail_atr_mult: f64,
    pub exit_time_stop_minutes: u32,
    pub exit_eod_flatten: bool,

    // Filters
    pub filter_adx_min: f64,
    pub filter_rvol_min: f64,
    pub filter_spread_max_bps: u32,
    pub filter_hurst_min: f64,

    // Session eligibility
    pub session_eligible: Vec<String>,
    pub session_blocked: Vec<String>,

    // Regime eligibility
    pub regime_eligible: Vec<String>,
    pub regime_blocked: Vec<String>,

    // Sizing
    pub sizing_mult: f64,

    // Ticker eligibility
    #[serde(default)]
    pub ticker_preferred: Vec<String>,

    // Adaptive ranges
    #[serde(default)]
    pub adaptive_ranges: CrossMarketMomentumAdaptiveRanges,
}

impl Default for CrossMarketMomentumConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            priority: 2,
            family: "momentum".to_string(),
            base_confidence: 65.0,
            entry_spy_direction_minutes: 15,
            entry_spy_min_move_pct: 0.3,
            entry_alignment: "same".to_string(),
            exit_trail_atr_mult: 1.5,
            exit_time_stop_minutes: 90,
            exit_eod_flatten: true,
            filter_adx_min: 20.0,
            filter_rvol_min: 1.2,
            filter_spread_max_bps: 15,
            filter_hurst_min: 0.50,
            session_eligible: vec!["14:45-16:00".to_string()],
            session_blocked: vec!["08:00-14:30".to_string(), "16:00-16:30".to_string()],
            regime_eligible: vec!["trending".to_string()],
            regime_blocked: vec!["mean_reverting".to_string()],
            sizing_mult: 1.2,
            ticker_preferred: vec![
                "QQQ3.L".to_string(),
                "3LUS.L".to_string(),
                "NVD3.L".to_string(),
            ],
            adaptive_ranges: CrossMarketMomentumAdaptiveRanges::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct CrossMarketMomentumAdaptiveRanges {
    pub entry_spy_min_move_pct: AdaptiveRange,
    pub exit_trail_atr_mult: AdaptiveRange,
    pub base_confidence: AdaptiveRange,
}

impl Default for CrossMarketMomentumAdaptiveRanges {
    fn default() -> Self {
        Self {
            entry_spy_min_move_pct: AdaptiveRange(0.2, 0.5),
            exit_trail_atr_mult: AdaptiveRange(1.0, 2.5),
            base_confidence: AdaptiveRange(55.0, 80.0),
        }
    }
}

// ============================================================================
// S21: Intraday Momentum
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct IntradayMomentumConfig {
    pub enabled: bool,
    pub priority: u32,
    pub family: String,
    pub base_confidence: f64,

    // Entry conditions
    pub entry_signal_window: String,
    pub entry_min_signal_pct: f64,
    pub entry_timing: String,

    // Exit conditions
    pub exit_timing: String,
    pub exit_stop_pct: f64,

    // Filters
    pub filter_volatile_day_boost: bool,
    pub filter_high_volume_day: bool,
    pub filter_spread_max_bps: u32,

    // Session eligibility
    pub session_eligible: Vec<String>,
    pub session_blocked: Vec<String>,

    // Regime eligibility
    pub regime_eligible: Vec<String>,
    pub regime_blocked: Vec<String>,

    // Sizing
    pub sizing_mult: f64,

    // Ticker eligibility
    #[serde(default)]
    pub ticker_whitelist: Vec<String>,

    // Adaptive ranges
    #[serde(default)]
    pub adaptive_ranges: IntradayMomentumAdaptiveRanges,
}

impl Default for IntradayMomentumConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            priority: 4,
            family: "momentum".to_string(),
            base_confidence: 60.0,
            entry_signal_window: "first_30min".to_string(),
            entry_min_signal_pct: 0.3,
            entry_timing: "15:30 ET".to_string(),
            exit_timing: "16:00 ET".to_string(),
            exit_stop_pct: 1.5,
            filter_volatile_day_boost: true,
            filter_high_volume_day: true,
            filter_spread_max_bps: 15,
            session_eligible: vec!["20:30-21:00".to_string()],
            session_blocked: Vec::new(),
            regime_eligible: vec!["trending".to_string(), "random".to_string()],
            regime_blocked: vec!["mean_reverting".to_string()],
            sizing_mult: 0.7,
            ticker_whitelist: vec![
                "QQQ3.L".to_string(),
                "3LUS.L".to_string(),
                "NVD3.L".to_string(),
                "TSL3.L".to_string(),
            ],
            adaptive_ranges: IntradayMomentumAdaptiveRanges::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct IntradayMomentumAdaptiveRanges {
    pub entry_min_signal_pct: AdaptiveRange,
    pub exit_stop_pct: AdaptiveRange,
    pub base_confidence: AdaptiveRange,
}

impl Default for IntradayMomentumAdaptiveRanges {
    fn default() -> Self {
        Self {
            entry_min_signal_pct: AdaptiveRange(0.2, 0.5),
            exit_stop_pct: AdaptiveRange(1.0, 3.0),
            base_confidence: AdaptiveRange(50.0, 75.0),
        }
    }
}

// ============================================================================
// Blackout Windows
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
pub struct BlackoutWindow {
    pub start: String,
    pub end: String,
    pub reason: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct BlackoutWindowsConfig {
    pub windows: Vec<BlackoutWindow>,
}

impl Default for BlackoutWindowsConfig {
    fn default() -> Self {
        Self {
            windows: vec![
                BlackoutWindow {
                    start: "07:50".to_string(),
                    end: "08:00".to_string(),
                    reason: "LSE opening auction".to_string(),
                },
                BlackoutWindow {
                    start: "16:30".to_string(),
                    end: "16:35".to_string(),
                    reason: "LSE closing auction".to_string(),
                },
            ],
        }
    }
}

// ============================================================================
// Spread Monitoring
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct SpreadMonitoringConfig {
    pub entry_suppress_spread_bps: u32,
    pub stop_suppress_spread_mult: f64,
    pub spread_refresh_secs: u32,
    pub spread_lookback_bars: u32,
}

impl Default for SpreadMonitoringConfig {
    fn default() -> Self {
        Self {
            entry_suppress_spread_bps: 25,
            stop_suppress_spread_mult: 2.0,
            spread_refresh_secs: 30,
            spread_lookback_bars: 100,
        }
    }
}

// ============================================================================
// Session Aggressiveness
// ============================================================================

/// Session-specific sizing multipliers. Keys are time windows like "08:00-08:30".
pub type SessionAggressivenessConfig = HashMap<String, f64>;

fn default_session_aggressiveness() -> SessionAggressivenessConfig {
    let mut m = HashMap::new();
    m.insert("08:00-08:30".to_string(), 0.5);
    m.insert("08:30-10:30".to_string(), 1.0);
    m.insert("10:30-14:30".to_string(), 0.8);
    m.insert("14:30-16:00".to_string(), 1.2);
    m.insert("16:00-16:30".to_string(), 0.3);
    m.insert("20:00-21:00".to_string(), 0.7);
    m
}

// ============================================================================
// Ticker Ranking
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct TickerRankingConfig {
    pub refresh_interval_minutes: u32,
    pub max_ranked_tickers: u32,
    #[serde(default)]
    pub current: HashMap<String, u32>,
}

impl Default for TickerRankingConfig {
    fn default() -> Self {
        let mut current = HashMap::new();
        current.insert("QQQ3.L".to_string(), 95);
        current.insert("3LUS.L".to_string(), 90);
        current.insert("NVD3.L".to_string(), 85);
        current.insert("3SEM.L".to_string(), 75);
        current.insert("TSL3.L".to_string(), 70);
        current.insert("QQQS.L".to_string(), 65);
        current.insert("3USS.L".to_string(), 60);
        current.insert("QQQ5.L".to_string(), 55);
        current.insert("TSM3.L".to_string(), 50);
        current.insert("MU2.L".to_string(), 45);
        current.insert("GPT3.L".to_string(), 40);
        current.insert("SP5L.L".to_string(), 35);
        Self {
            refresh_interval_minutes: 120,
            max_ranked_tickers: 100,
            current,
        }
    }
}

// ============================================================================
// Global Settings
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct GlobalStrategyConfig {
    pub max_active_strategies: u32,
    pub confidence_floor: f64,
    pub priority_order: Vec<String>,
}

impl Default for GlobalStrategyConfig {
    fn default() -> Self {
        Self {
            max_active_strategies: 3,
            confidence_floor: 45.0,
            priority_order: vec![
                "gap_fade".to_string(),
                "vwap_dip_buy".to_string(),
                "cross_market_momentum".to_string(),
                "intraday_momentum".to_string(),
                "rsi_ibs".to_string(),
            ],
        }
    }
}

// ============================================================================
// Strategy Map (holds all five strategies)
// ============================================================================

#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct StrategyMap {
    pub vwap_dip_buy: VwapDipBuyConfig,
    pub gap_fade: GapFadeConfig,
    pub rsi_ibs: RsiIbsConfig,
    pub cross_market_momentum: CrossMarketMomentumConfig,
    pub intraday_momentum: IntradayMomentumConfig,
}

impl Default for StrategyMap {
    fn default() -> Self {
        Self {
            vwap_dip_buy: VwapDipBuyConfig::default(),
            gap_fade: GapFadeConfig::default(),
            rsi_ibs: RsiIbsConfig::default(),
            cross_market_momentum: CrossMarketMomentumConfig::default(),
            intraday_momentum: IntradayMomentumConfig::default(),
        }
    }
}

// ============================================================================
// Top-level: Strategy Registry
// ============================================================================

/// The complete strategy configuration, deserialized from strategies.toml.
///
/// Holds global settings, per-strategy configs (with adaptive ranges),
/// blackout windows, spread monitoring, session aggressiveness, and ticker ranking.
#[derive(Debug, Clone, Deserialize)]
#[serde(default)]
pub struct StrategyRegistry {
    pub schema_version: u32,
    pub global: GlobalStrategyConfig,

    /// Per-strategy configurations, keyed under `[strategy.<name>]` in TOML.
    #[serde(rename = "strategy")]
    pub strategies: StrategyMap,

    pub blackout_windows: BlackoutWindowsConfig,
    pub spread_monitoring: SpreadMonitoringConfig,

    #[serde(default = "default_session_aggressiveness")]
    pub session_aggressiveness: SessionAggressivenessConfig,

    pub ticker_ranking: TickerRankingConfig,
}

impl Default for StrategyRegistry {
    fn default() -> Self {
        Self {
            schema_version: 1,
            global: GlobalStrategyConfig::default(),
            strategies: StrategyMap::default(),
            blackout_windows: BlackoutWindowsConfig::default(),
            spread_monitoring: SpreadMonitoringConfig::default(),
            session_aggressiveness: default_session_aggressiveness(),
            ticker_ranking: TickerRankingConfig::default(),
        }
    }
}

impl StrategyRegistry {
    /// Return all enabled strategy names in priority order.
    pub fn enabled_strategies_by_priority(&self) -> Vec<&str> {
        let mut candidates: Vec<(&str, u32, bool)> = vec![
            ("vwap_dip_buy", self.strategies.vwap_dip_buy.priority, self.strategies.vwap_dip_buy.enabled),
            ("gap_fade", self.strategies.gap_fade.priority, self.strategies.gap_fade.enabled),
            ("rsi_ibs", self.strategies.rsi_ibs.priority, self.strategies.rsi_ibs.enabled),
            (
                "cross_market_momentum",
                self.strategies.cross_market_momentum.priority,
                self.strategies.cross_market_momentum.enabled,
            ),
            (
                "intraday_momentum",
                self.strategies.intraday_momentum.priority,
                self.strategies.intraday_momentum.enabled,
            ),
        ];
        // Filter to enabled only
        candidates.retain(|(_, _, enabled)| *enabled);
        // Sort by priority ascending (1 = highest)
        candidates.sort_by_key(|(_, prio, _)| *prio);
        candidates.iter().map(|(name, _, _)| *name).collect()
    }

    /// Lookup the session aggressiveness multiplier for a given time window string.
    /// Returns 1.0 if no matching window is found.
    pub fn session_sizing_mult(&self, window: &str) -> f64 {
        self.session_aggressiveness
            .get(window)
            .copied()
            .unwrap_or(1.0)
    }

    /// Lookup ticker ranking score. Returns 0 if ticker is unranked.
    pub fn ticker_score(&self, ticker: &str) -> u32 {
        self.ticker_ranking
            .current
            .get(ticker)
            .copied()
            .unwrap_or(0)
    }

    /// Check whether a given time (HH:MM format) falls inside any blackout window.
    /// Simple string comparison — assumes HH:MM format, same-day windows only.
    pub fn is_blackout(&self, time_hhmm: &str) -> bool {
        self.blackout_windows.windows.iter().any(|w| {
            time_hhmm >= w.start.as_str() && time_hhmm < w.end.as_str()
        })
    }
}

// ============================================================================
// Loader function
// ============================================================================

/// Load strategy configuration from `config_dir/strategies.toml`.
///
/// Returns a fully populated `StrategyRegistry`. Missing fields use defaults.
///
/// # Errors
/// Returns `ConfigError::Io` if the file cannot be read, or `ConfigError::Parse`
/// if the TOML is malformed.
pub fn load_strategies(config_dir: &Path) -> Result<StrategyRegistry, ConfigError> {
    let path = config_dir.join("strategies.toml");
    let content = std::fs::read_to_string(&path)?;
    let registry: StrategyRegistry =
        toml::from_str(&content).map_err(|e| ConfigError::Parse(format!("{path:?}: {e}")))?;
    eprintln!(
        "STRATEGY_CONFIG: Loaded {} strategies ({} enabled), schema v{}",
        5,
        registry.enabled_strategies_by_priority().len(),
        registry.schema_version,
    );
    Ok(registry)
}

// ============================================================================
// Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn config_dir() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config")
    }

    #[test]
    fn test_load_strategies_from_toml() {
        let registry = load_strategies(&config_dir()).expect("load strategies.toml");
        assert_eq!(registry.schema_version, 1);
    }

    #[test]
    fn test_global_settings() {
        let reg = load_strategies(&config_dir()).expect("load");
        assert_eq!(reg.global.max_active_strategies, 2); // Audit-adjusted from 3
        assert!((reg.global.confidence_floor - 55.0).abs() < f64::EPSILON); // Audit-raised from 45
        assert_eq!(reg.global.priority_order.len(), 5);
        assert_eq!(reg.global.priority_order[0], "cross_market_momentum"); // Audit: primary strategy
    }

    #[test]
    fn test_vwap_dip_buy_config() {
        let reg = load_strategies(&config_dir()).expect("load");
        let s = &reg.strategies.vwap_dip_buy;
        assert!(s.enabled);
        assert_eq!(s.priority, 2);
        assert_eq!(s.family, "mean_reversion");
        assert!((s.base_confidence - 70.0).abs() < f64::EPSILON);
        assert!((s.entry_vwap_sigma - 2.0).abs() < f64::EPSILON);
        assert!((s.entry_scale_in_sigma - 3.0).abs() < f64::EPSILON);
        assert_eq!(s.entry_volume_filter, "declining");
        assert!((s.exit_stop_sigma - 3.0).abs() < f64::EPSILON);
        assert_eq!(s.exit_time_stop_minutes, 90);
        assert!(s.exit_eod_flatten);
        assert!((s.filter_adx_max - 25.0).abs() < f64::EPSILON);
        assert!((s.filter_hurst_max - 0.50).abs() < f64::EPSILON);
        assert_eq!(s.filter_spread_max_bps, 15);
        assert_eq!(s.session_eligible.len(), 2);
        assert_eq!(s.regime_blocked, vec!["trending"]);
        assert!((s.sizing_mult - 1.0).abs() < f64::EPSILON);
        assert_eq!(s.sizing_scale_in, vec![0.6, 0.4]);
    }

    #[test]
    fn test_vwap_dip_buy_adaptive_ranges() {
        let reg = load_strategies(&config_dir()).expect("load");
        let ar = &reg.strategies.vwap_dip_buy.adaptive_ranges;
        assert!((ar.entry_vwap_sigma.min() - 1.5).abs() < f64::EPSILON);
        assert!((ar.entry_vwap_sigma.max() - 3.0).abs() < f64::EPSILON);
        assert!((ar.exit_stop_sigma.min() - 2.5).abs() < f64::EPSILON);
        assert!((ar.base_confidence.min() - 55.0).abs() < f64::EPSILON);
        // Clamp test
        assert!((ar.entry_vwap_sigma.clamp(0.5) - 1.5).abs() < f64::EPSILON);
        assert!((ar.entry_vwap_sigma.clamp(10.0) - 3.0).abs() < f64::EPSILON);
        assert!((ar.entry_vwap_sigma.clamp(2.0) - 2.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_gap_fade_config() {
        let reg = load_strategies(&config_dir()).expect("load");
        let s = &reg.strategies.gap_fade;
        assert!(s.enabled);
        assert_eq!(s.priority, 1);
        assert!((s.base_confidence - 72.0).abs() < f64::EPSILON);
        assert!((s.entry_min_gap_pct - 1.5).abs() < f64::EPSILON);
        assert!((s.entry_max_gap_pct - 6.0).abs() < f64::EPSILON);
        assert_eq!(s.entry_direction, "fade");
        assert_eq!(s.entry_delay_minutes, 15);
        assert!((s.exit_target_fill_pct - 0.75).abs() < f64::EPSILON);
        assert_eq!(s.exit_time_stop_minutes, 120);
        assert!(s.filter_no_earnings);
        assert!((s.sizing_mult - 0.8).abs() < f64::EPSILON);
        // Day confidence adjustments
        assert!((s.day_confidence_adj["Mon"] - (-5.0)).abs() < f64::EPSILON);
        assert!((s.day_confidence_adj["Fri"] - 5.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_rsi_ibs_config() {
        let reg = load_strategies(&config_dir()).expect("load");
        let s = &reg.strategies.rsi_ibs;
        assert!(s.enabled);
        assert_eq!(s.priority, 3);
        assert!((s.base_confidence - 75.0).abs() < f64::EPSILON);
        assert_eq!(s.entry_rsi_period, 2);
        assert!((s.entry_rsi_threshold - 5.0).abs() < f64::EPSILON);
        assert!((s.entry_ibs_threshold - 0.20).abs() < f64::EPSILON);
        assert!(s.entry_above_sma200);
        assert_eq!(s.exit_rule, "close_above_5sma");
        assert_eq!(s.exit_max_hold_days, 10);
        assert!((s.sizing_mult - 0.6).abs() < f64::EPSILON);
        assert!((s.sizing_3x_penalty - 0.5).abs() < f64::EPSILON);
        assert_eq!(s.ticker_whitelist.len(), 4);
        assert!(s.ticker_whitelist.contains(&"QQQ3.L".to_string()));
    }

    #[test]
    fn test_cross_market_momentum_config() {
        let reg = load_strategies(&config_dir()).expect("load");
        let s = &reg.strategies.cross_market_momentum;
        assert!(s.enabled);
        assert_eq!(s.priority, 2);
        assert_eq!(s.family, "momentum");
        assert!((s.base_confidence - 65.0).abs() < f64::EPSILON);
        assert_eq!(s.entry_spy_direction_minutes, 15);
        assert!((s.entry_spy_min_move_pct - 0.3).abs() < f64::EPSILON);
        assert_eq!(s.entry_alignment, "same");
        assert!((s.exit_trail_atr_mult - 1.5).abs() < f64::EPSILON);
        assert_eq!(s.exit_time_stop_minutes, 90);
        assert!((s.filter_adx_min - 20.0).abs() < f64::EPSILON);
        assert!((s.filter_hurst_min - 0.50).abs() < f64::EPSILON);
        assert!((s.sizing_mult - 1.2).abs() < f64::EPSILON);
        assert_eq!(s.regime_eligible, vec!["trending"]);
    }

    #[test]
    fn test_intraday_momentum_config() {
        let reg = load_strategies(&config_dir()).expect("load");
        let s = &reg.strategies.intraday_momentum;
        assert!(s.enabled);
        assert_eq!(s.priority, 4);
        assert_eq!(s.family, "momentum");
        assert!((s.base_confidence - 60.0).abs() < f64::EPSILON);
        assert_eq!(s.entry_signal_window, "first_30min");
        assert!((s.entry_min_signal_pct - 0.3).abs() < f64::EPSILON);
        assert!((s.exit_stop_pct - 1.5).abs() < f64::EPSILON);
        assert!(s.filter_volatile_day_boost);
        assert!((s.sizing_mult - 0.7).abs() < f64::EPSILON);
        assert_eq!(s.ticker_whitelist.len(), 4);
    }

    #[test]
    fn test_blackout_windows() {
        let reg = load_strategies(&config_dir()).expect("load");
        assert_eq!(reg.blackout_windows.windows.len(), 2);
        assert_eq!(reg.blackout_windows.windows[0].start, "07:50");
        assert_eq!(reg.blackout_windows.windows[0].end, "08:00");
        assert_eq!(reg.blackout_windows.windows[1].start, "16:30");
        // is_blackout helper
        assert!(reg.is_blackout("07:55"));
        assert!(!reg.is_blackout("08:00"));
        assert!(reg.is_blackout("16:32"));
        assert!(!reg.is_blackout("12:00"));
    }

    #[test]
    fn test_spread_monitoring() {
        let reg = load_strategies(&config_dir()).expect("load");
        assert_eq!(reg.spread_monitoring.entry_suppress_spread_bps, 25);
        assert!((reg.spread_monitoring.stop_suppress_spread_mult - 2.0).abs() < f64::EPSILON);
        assert_eq!(reg.spread_monitoring.spread_refresh_secs, 30);
        assert_eq!(reg.spread_monitoring.spread_lookback_bars, 100);
    }

    #[test]
    fn test_session_aggressiveness() {
        let reg = load_strategies(&config_dir()).expect("load");
        assert!((reg.session_sizing_mult("08:00-08:30") - 0.5).abs() < f64::EPSILON);
        assert!((reg.session_sizing_mult("14:30-16:00") - 1.2).abs() < f64::EPSILON);
        assert!((reg.session_sizing_mult("16:00-16:30") - 0.3).abs() < f64::EPSILON);
        // Unknown window returns 1.0
        assert!((reg.session_sizing_mult("03:00-04:00") - 1.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_ticker_ranking() {
        let reg = load_strategies(&config_dir()).expect("load");
        assert_eq!(reg.ticker_ranking.refresh_interval_minutes, 120);
        assert_eq!(reg.ticker_ranking.max_ranked_tickers, 100);
        assert_eq!(reg.ticker_score("QQQ3.L"), 95);
        assert_eq!(reg.ticker_score("3LUS.L"), 90);
        assert_eq!(reg.ticker_score("SP5L.L"), 35);
        // Unknown ticker returns 0
        assert_eq!(reg.ticker_score("FAKE.L"), 0);
    }

    #[test]
    fn test_enabled_strategies_by_priority() {
        let reg = load_strategies(&config_dir()).expect("load");
        let enabled = reg.enabled_strategies_by_priority();
        // All 5 are enabled in the default config
        assert_eq!(enabled.len(), 5);
        // gap_fade has priority 1, should be first
        assert_eq!(enabled[0], "gap_fade");
        // intraday_momentum has priority 4, should be last
        assert_eq!(enabled[4], "intraday_momentum");
    }

    #[test]
    fn test_defaults_used_for_missing_fields() {
        // Parse a minimal TOML — all fields should fall back to defaults.
        let minimal = r#"
            schema_version = 1
            [global]
            max_active_strategies = 2
        "#;
        let reg: StrategyRegistry = toml::from_str(minimal).expect("parse minimal");
        assert_eq!(reg.schema_version, 1);
        assert_eq!(reg.global.max_active_strategies, 2);
        // confidence_floor falls back to default
        assert!((reg.global.confidence_floor - 45.0).abs() < f64::EPSILON);
        // Strategies fall back to defaults
        assert!(reg.strategies.vwap_dip_buy.enabled);
        assert!((reg.strategies.gap_fade.base_confidence - 72.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_adaptive_range_clamp() {
        let range = AdaptiveRange(10.0, 50.0);
        assert!((range.clamp(5.0) - 10.0).abs() < f64::EPSILON);
        assert!((range.clamp(30.0) - 30.0).abs() < f64::EPSILON);
        assert!((range.clamp(100.0) - 50.0).abs() < f64::EPSILON);
    }
}
