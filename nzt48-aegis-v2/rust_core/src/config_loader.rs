//! TOML configuration loader. Parses config/, contracts, universe, holidays.
//! Single entry point: `EngineConfig::load(config_dir)` loads everything.

use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

use crate::config::RiskConfig;

// ── Error type ──

#[derive(Debug)]
pub enum ConfigError {
    Io(std::io::Error),
    Parse(String),
}

impl From<std::io::Error> for ConfigError {
    fn from(e: std::io::Error) -> Self {
        ConfigError::Io(e)
    }
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Io(e) => write!(f, "config IO: {e}"),
            ConfigError::Parse(msg) => write!(f, "config parse: {msg}"),
        }
    }
}

// ── Raw TOML deserialization structs ──

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct RawConfig {
    signal: RawSignal,
    position: RawPosition,
    kelly: RawKelly,
    timing: RawTiming,
    risk: RawRisk,
    /// Q-051: Unified cost model section.
    #[serde(default)]
    costs: CostsConfig,
    execution: ExecutionConfig,
    channel: RawChannel,
    backpressure: BackpressureConfig,
    reconciliation: ReconciliationConfig,
    ibkr: IbkrConfig,
    rotation: RotationConfig,
    wal: RawWal,
    inverse_pairs: RawInversePairs,
    sectors: HashMap<String, Vec<String>>,
    crucible: CrucibleConfig,
    /// Sprint 6: Chandelier exit ladder config.
    #[serde(default)]
    chandelier: RawChandelier,
    /// Sprint 6: Entry type base confidences and thresholds.
    #[serde(default)]
    entry_types: RawEntryTypes,
    /// Sprint 6: Risk arbiter hardening — previously hardcoded constants.
    #[serde(default)]
    hardening: RawHardening,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct RawSignal {
    confidence_floor: f64,
    outlier_win_cap_pct: f64,
    gap_detection_pct: f64,
    erroneous_tick_deviation_pct: f64,
    velocity_check_window_secs: u32,
    velocity_check_max_intents: u32,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct RawPosition {
    max_simultaneous_positions: u32,
    portfolio_heat_limit_pct: f64,
    sector_heat_cap_pct: f64,
    cash_buffer_pct: f64,
    isa_annual_limit_gbp: f64,
    isa_tax_year_start: String,
    /// R6: Dividend withholding tax factor (UK ISA: 0.85 = 15% withholding).
    #[serde(default = "default_dividend_withholding")]
    dividend_withholding_factor: f64,
    /// P3.4: Per-exchange minimum entry notional (exchange name → local currency amount).
    #[serde(default)]
    min_entry_per_exchange: HashMap<String, f64>,
}

fn default_dividend_withholding() -> f64 { 0.85 }

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct RawKelly {
    fraction_cap: f64,
    clamp_max: f64,
    volatility_drag_3x: u32,
    volatility_drag_5x: u32,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct RawTiming {
    stale_data_threshold_secs: u64,
    entry_cutoff_london: String,
    lse_open_london: String,
    lse_close_london: String,
    auction_open_start: String,
    auction_open_end: String,
    auction_close_start: String,
    auction_close_end: String,
    eod_flatten_time: String,
    eod_flatten_phase1: String,
    eod_flatten_phase2: String,
    eod_flatten_phase3: String,
    gap_cooldown_mins: u32,
    synthetic_halt_limp_secs: u32,
    synthetic_halt_full_secs: u32,
    /// Sprint 7: Per-exchange entry cutoffs (exchange name → "HH:MM" local time).
    #[serde(default)]
    exchange_cutoffs: HashMap<String, String>,
    /// P3.3: Per-exchange stale data threshold (exchange name → seconds).
    #[serde(default)]
    stale_data_per_exchange: HashMap<String, u64>,
    /// P3.5: Economic blackout window per event type (event name → minutes).
    #[serde(default)]
    economic_blackout_minutes: HashMap<String, u32>,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct RawRisk {
    daily_drawdown_pct: f64,
    spread_veto_pct: f64,
    slippage_assumption_pct: f64,
    consecutive_loss_halt: u32,
    reject_to_halt_count: u32,
    reject_to_halt_window_secs: u32,
    /// N0a: Daily trade frequency limit.
    #[serde(default = "default_daily_trade_limit")]
    max_daily_trades: u32,
    /// N0d: Minimum gross edge to justify entry.
    #[serde(default = "default_min_gross_edge_pct")]
    min_gross_edge_pct: f64,
    /// Sprint 10: Portfolio risk gates.
    #[serde(default = "default_weekly_dd")]
    weekly_drawdown_pct: f64,
    #[serde(default = "default_peak_dd")]
    peak_drawdown_halt_pct: f64,
    #[serde(default = "default_eq_floor")]
    equity_floor_pct: f64,
    #[serde(default = "default_overnight")]
    overnight_exposure_cap_pct: f64,
    #[serde(default = "default_max_corr")]
    max_correlated_positions: u32,
    #[serde(default = "default_risk_per_trade")]
    max_risk_per_trade_pct: f64,
}

fn default_weekly_dd() -> f64 { 7.0 }
fn default_peak_dd() -> f64 { 15.0 }
fn default_eq_floor() -> f64 { 70.0 }
fn default_overnight() -> f64 { 50.0 }
fn default_max_corr() -> u32 { 3 }
fn default_risk_per_trade() -> f64 { 0.75 }

fn default_daily_trade_limit() -> u32 { 3 }
fn default_min_gross_edge_pct() -> f64 { 0.15 }

/// Q-051: Unified cost model — single source of truth for all trading costs.
#[derive(Debug, Clone, Deserialize)]
pub struct CostsConfig {
    /// Round-trip trading cost as fraction (entry + exit spread + commission amortized).
    #[serde(default = "default_round_trip_fee_pct")]
    pub round_trip_fee_pct: f64,
    /// IBKR tiered commission minimum per trade (GBP).
    #[serde(default = "default_ibkr_commission_gbp")]
    pub ibkr_commission_gbp: f64,
    /// Stamp duty fraction (0 for ETPs, 0.005 for UK equities).
    #[serde(default)]
    pub stamp_duty_pct: f64,
    /// Financial Transaction Tax fraction.
    #[serde(default)]
    pub ftt_pct: f64,
    /// FX conversion cost fraction (for USD-denominated LSE ETPs).
    #[serde(default = "default_fx_conversion_pct")]
    pub fx_conversion_pct: f64,
    /// P3.1: Per-exchange round-trip cost as fraction (exchange name → fee fraction).
    #[serde(default)]
    pub per_exchange: HashMap<String, f64>,
}

fn default_round_trip_fee_pct() -> f64 { 0.003 }
fn default_ibkr_commission_gbp() -> f64 { 1.70 }
fn default_fx_conversion_pct() -> f64 { 0.002 }

impl Default for CostsConfig {
    fn default() -> Self {
        Self {
            round_trip_fee_pct: default_round_trip_fee_pct(),
            ibkr_commission_gbp: default_ibkr_commission_gbp(),
            stamp_duty_pct: 0.0,
            ftt_pct: 0.0,
            fx_conversion_pct: default_fx_conversion_pct(),
            per_exchange: HashMap::new(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ExecutionConfig {
    pub marketable_limit_buffer_pct: f64,
    pub tick_size_under_1: f64,
    pub tick_size_over_1: f64,
}

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct RawChannel {
    capacity: usize,
    reduce_threshold: usize,
    halt_threshold: usize,
    tick_drop_alert_per_sec: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BackpressureConfig {
    pub warning_ms: u64,
    pub reduce_ms: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ReconciliationConfig {
    pub interval_secs: u64,
    pub orphan_ack_timeout_secs: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct IbkrConfig {
    pub client_id_executioner: u32,
    pub client_id_ouroboros: u32,
    pub reconnect_backoff_secs: Vec<u64>,
    pub rate_limit_msgs_per_sec: u32,
    pub reqmktdata_pacing_ms: u64,
    pub historical_data_max_per_10min: u32,
    pub max_simultaneous_lines: u32,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RotationConfig {
    pub tier1_permanent_lines: u32,
    pub tier2_rotating_lines: u32,
    pub tier2_rotation_secs: u32,
    pub tier2_vanguard_batches: u32,
    pub tier3_apex_batches: u32,
    pub tier1_promotion_confidence: f64,
    pub full_vanguard_scan_mins: u32,
    pub full_apex_scan_mins: u32,
    pub open_position_always_tier1: bool,
}

#[derive(Debug, Deserialize)]
struct RawWal {
    schema_version: u8,
}

#[derive(Debug, Deserialize)]
struct RawInversePairs {
    pairs: Vec<[String; 2]>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CrucibleConfig {
    pub max_positions_override: u32,
    pub paper_mode: bool,
    pub starting_equity_gbp: f64,
    /// P2-3.3: When true, paper mode uses live risk gates (max_positions=3, heat=10%, etc.)
    /// instead of relaxed paper limits. Enables realistic validation before going live.
    #[serde(default)]
    pub paper_uses_live_gates: bool,
}

// ── Sprint 6: Chandelier config ──

#[derive(Debug, Deserialize)]
pub struct RawChandelier {
    #[serde(default = "default_rung_pct")]
    pub rung_pct: Vec<f64>,
    #[serde(default = "default_initial_stop_atr")]
    pub initial_stop_atr_mult: f64,
    #[serde(default = "default_rung3_trail")]
    pub rung3_trail_atr: f64,
    #[serde(default = "default_rung4_trail")]
    pub rung4_trail_atr: f64,
    #[serde(default = "default_rung5_trail")]
    pub rung5_trail_atr: f64,
    #[serde(default = "default_atr_floor")]
    pub atr_floor_pct: f64,
    #[serde(default = "default_spike_pct")]
    pub price_spike_pct: f64,
    #[serde(default = "default_dust")]
    pub dust_threshold_gbp: f64,
    #[serde(default)]
    pub adaptive: RawChandelierAdaptive,
}

impl Default for RawChandelier {
    fn default() -> Self {
        Self {
            rung_pct: default_rung_pct(),
            initial_stop_atr_mult: default_initial_stop_atr(),
            rung3_trail_atr: default_rung3_trail(),
            rung4_trail_atr: default_rung4_trail(),
            rung5_trail_atr: default_rung5_trail(),
            atr_floor_pct: default_atr_floor(),
            price_spike_pct: default_spike_pct(),
            dust_threshold_gbp: default_dust(),
            adaptive: RawChandelierAdaptive::default(),
        }
    }
}

fn default_rung_pct() -> Vec<f64> { vec![0.0, 0.008, 0.015, 0.025, 0.040] }
fn default_initial_stop_atr() -> f64 { 2.0 }
fn default_rung3_trail() -> f64 { 1.0 }
fn default_rung4_trail() -> f64 { 0.75 }
fn default_rung5_trail() -> f64 { 0.5 }
fn default_atr_floor() -> f64 { 0.005 }
fn default_spike_pct() -> f64 { 0.10 }
fn default_dust() -> f64 { 500.0 }

#[derive(Debug, Deserialize)]
pub struct RawChandelierAdaptive {
    #[serde(default = "default_vol_range")]
    pub volatility_range: [f64; 2],
    #[serde(default = "default_vol_low")]
    pub volatility_ann_low: f64,
    #[serde(default = "default_vol_high")]
    pub volatility_ann_high: f64,
    #[serde(default = "default_time_range")]
    pub time_decay_range: [f64; 2],
    #[serde(default = "default_time_slope")]
    pub time_decay_slope: f64,
    #[serde(default = "default_mom_range")]
    pub momentum_range: [f64; 2],
    #[serde(default = "default_mom_sens")]
    pub momentum_sensitivity: f64,
    #[serde(default = "default_liq_range")]
    pub liquidity_range: [f64; 2],
    #[serde(default = "default_liq_sens")]
    pub liquidity_sensitivity: f64,
    #[serde(default = "default_heat_range")]
    pub heat_range: [f64; 2],
    #[serde(default = "default_heat_low")]
    pub heat_low_pct: f64,
    #[serde(default = "default_heat_high")]
    pub heat_high_pct: f64,
    #[serde(default = "default_regime_reduce")]
    pub regime_reduce_mult: f64,
    #[serde(default = "default_mega_thresh")]
    pub mega_runner_threshold_atr: f64,
    #[serde(default = "default_mega_slope")]
    pub mega_runner_slope: f64,
    #[serde(default = "default_mega_range")]
    pub mega_runner_range: [f64; 2],
}

impl Default for RawChandelierAdaptive {
    fn default() -> Self {
        Self {
            volatility_range: default_vol_range(),
            volatility_ann_low: default_vol_low(),
            volatility_ann_high: default_vol_high(),
            time_decay_range: default_time_range(),
            time_decay_slope: default_time_slope(),
            momentum_range: default_mom_range(),
            momentum_sensitivity: default_mom_sens(),
            liquidity_range: default_liq_range(),
            liquidity_sensitivity: default_liq_sens(),
            heat_range: default_heat_range(),
            heat_low_pct: default_heat_low(),
            heat_high_pct: default_heat_high(),
            regime_reduce_mult: default_regime_reduce(),
            mega_runner_threshold_atr: default_mega_thresh(),
            mega_runner_slope: default_mega_slope(),
            mega_runner_range: default_mega_range(),
        }
    }
}

fn default_vol_range() -> [f64; 2] { [0.8, 1.5] }
fn default_vol_low() -> f64 { 0.20 }
fn default_vol_high() -> f64 { 0.50 }
fn default_time_range() -> [f64; 2] { [0.8, 1.0] }
fn default_time_slope() -> f64 { 0.2 }
fn default_mom_range() -> [f64; 2] { [1.0, 1.3] }
fn default_mom_sens() -> f64 { 10.0 }
fn default_liq_range() -> [f64; 2] { [1.0, 1.4] }
fn default_liq_sens() -> f64 { 40.0 }
fn default_heat_range() -> [f64; 2] { [0.7, 1.0] }
fn default_heat_low() -> f64 { 2.0 }
fn default_heat_high() -> f64 { 8.0 }
fn default_regime_reduce() -> f64 { 0.6 }
fn default_mega_thresh() -> f64 { 3.0 }
fn default_mega_slope() -> f64 { 0.2 }
fn default_mega_range() -> [f64; 2] { [1.0, 2.0] }

// ── Sprint 6: Entry types config ──

#[derive(Debug, Deserialize)]
pub struct RawEntryTypes {
    #[serde(default = "default_type_a_conf")] pub type_a_confidence: f64,
    #[serde(default = "default_type_b_conf")] pub type_b_confidence: f64,
    #[serde(default = "default_type_c_conf")] pub type_c_confidence: f64,
    #[serde(default = "default_type_d_conf")] pub type_d_confidence: f64,
    #[serde(default = "default_type_a_rsi")] pub type_a_rsi_oversold: f64,
    #[serde(default = "default_type_a_vol")] pub type_a_volume_spike_mult: f64,
    #[serde(default = "default_type_a_drop")] pub type_a_drop_atr_mult: f64,
    #[serde(default = "default_type_b_rsi_lo")] pub type_b_rsi_low: f64,
    #[serde(default = "default_type_b_rsi_hi")] pub type_b_rsi_high: f64,
    #[serde(default = "default_type_b_bars")] pub type_b_momentum_bars: usize,
    #[serde(default = "default_type_c_rsi")] pub type_c_rsi_overbought: f64,
    #[serde(default = "default_type_d_prox")] pub type_d_price_proximity_pct: f64,
    #[serde(default = "default_type_d_rsi_lo")] pub type_d_rsi_low: f64,
    #[serde(default = "default_type_d_rsi_hi")] pub type_d_rsi_high: f64,
    #[serde(default = "default_decay_rate")] pub confidence_decay_rate_per_hour: f64,
}

impl Default for RawEntryTypes {
    fn default() -> Self {
        Self {
            type_a_confidence: default_type_a_conf(),
            type_b_confidence: default_type_b_conf(),
            type_c_confidence: default_type_c_conf(),
            type_d_confidence: default_type_d_conf(),
            type_a_rsi_oversold: default_type_a_rsi(),
            type_a_volume_spike_mult: default_type_a_vol(),
            type_a_drop_atr_mult: default_type_a_drop(),
            type_b_rsi_low: default_type_b_rsi_lo(),
            type_b_rsi_high: default_type_b_rsi_hi(),
            type_b_momentum_bars: default_type_b_bars(),
            type_c_rsi_overbought: default_type_c_rsi(),
            type_d_price_proximity_pct: default_type_d_prox(),
            type_d_rsi_low: default_type_d_rsi_lo(),
            type_d_rsi_high: default_type_d_rsi_hi(),
            confidence_decay_rate_per_hour: default_decay_rate(),
        }
    }
}

fn default_type_a_conf() -> f64 { 65.0 }
fn default_type_b_conf() -> f64 { 82.0 }
fn default_type_c_conf() -> f64 { 72.0 }
fn default_type_d_conf() -> f64 { 80.0 }
fn default_type_a_rsi() -> f64 { 40.0 }
fn default_type_a_vol() -> f64 { 1.8 }
fn default_type_a_drop() -> f64 { 2.0 }
fn default_type_b_rsi_lo() -> f64 { 30.0 }
fn default_type_b_rsi_hi() -> f64 { 70.0 }
fn default_type_b_bars() -> usize { 3 }
fn default_type_c_rsi() -> f64 { 75.0 }
fn default_type_d_prox() -> f64 { 1.0 }
fn default_type_d_rsi_lo() -> f64 { 20.0 }
fn default_type_d_rsi_hi() -> f64 { 40.0 }
fn default_decay_rate() -> f64 { 2.1 }

// ── Sprint 6: Hardening config ──

#[derive(Debug, Deserialize)]
pub struct RawHardening {
    #[serde(default = "default_sys_vel")] pub system_velocity_max: usize,
    #[serde(default = "default_kelly_target")] pub kelly_ramp_target: u32,
    #[serde(default = "default_kelly_clamp_min")] pub kelly_ramp_clamp_min: f64,
    #[serde(default = "default_kelly_clamp_max")] pub kelly_ramp_clamp_max: f64,
    #[serde(default = "default_vix_h_enter")] pub vix_high_enter: f64,
    #[serde(default = "default_vix_h_exit")] pub vix_high_exit: f64,
    #[serde(default = "default_vix_e_enter")] pub vix_extreme_enter: f64,
    #[serde(default = "default_vix_e_exit")] pub vix_extreme_exit: f64,
    #[serde(default = "default_garch_base")] pub garch_threshold_base: f64,
    #[serde(default = "default_cvar_mult")] pub cvar_heat_multiplier: f64,
    #[serde(default = "default_re3_ic")] pub reentry_3pos_ic: f64,
    #[serde(default = "default_re3_trades")] pub reentry_3pos_trades: u32,
    #[serde(default = "default_re2_ic")] pub reentry_2pos_ic: f64,
    #[serde(default = "default_re2_trades")] pub reentry_2pos_trades: u32,
    #[serde(default = "default_macro_stale")] pub macro_stress_stale_tick_secs: u64,
    #[serde(default = "default_dd_vel_pct")] pub drawdown_velocity_pct: f64,
    #[serde(default = "default_dd_vel_win")] pub drawdown_velocity_window_secs: u64,
    #[serde(default = "default_eq_snap_int")] pub equity_snapshot_interval_secs: u64,
    #[serde(default = "default_eq_snap_ret")] pub equity_snapshot_retention_secs: u64,
    #[serde(default = "default_spread_edge")] pub spread_edge_ratio: f64,
    #[serde(default = "default_scan_min")] pub scanner_score_min: f64,
    #[serde(default = "default_kelly_floor")] pub kelly_fraction_floor: f64,
    #[serde(default)]
    pub broker: RawBrokerHardening,
    #[serde(default)]
    pub ticks: RawTickHardening,
    #[serde(default)]
    pub sizing: RawSizingHardening,
}

impl Default for RawHardening {
    fn default() -> Self {
        Self {
            system_velocity_max: default_sys_vel(),
            kelly_ramp_target: default_kelly_target(),
            kelly_ramp_clamp_min: default_kelly_clamp_min(),
            kelly_ramp_clamp_max: default_kelly_clamp_max(),
            vix_high_enter: default_vix_h_enter(),
            vix_high_exit: default_vix_h_exit(),
            vix_extreme_enter: default_vix_e_enter(),
            vix_extreme_exit: default_vix_e_exit(),
            garch_threshold_base: default_garch_base(),
            cvar_heat_multiplier: default_cvar_mult(),
            reentry_3pos_ic: default_re3_ic(),
            reentry_3pos_trades: default_re3_trades(),
            reentry_2pos_ic: default_re2_ic(),
            reentry_2pos_trades: default_re2_trades(),
            macro_stress_stale_tick_secs: default_macro_stale(),
            drawdown_velocity_pct: default_dd_vel_pct(),
            drawdown_velocity_window_secs: default_dd_vel_win(),
            equity_snapshot_interval_secs: default_eq_snap_int(),
            equity_snapshot_retention_secs: default_eq_snap_ret(),
            spread_edge_ratio: default_spread_edge(),
            scanner_score_min: default_scan_min(),
            kelly_fraction_floor: default_kelly_floor(),
            broker: RawBrokerHardening::default(),
            ticks: RawTickHardening::default(),
            sizing: RawSizingHardening::default(),
        }
    }
}

fn default_sys_vel() -> usize { 10 }
fn default_kelly_target() -> u32 { 250 }
fn default_kelly_clamp_min() -> f64 { 0.1 }
fn default_kelly_clamp_max() -> f64 { 1.0 }
fn default_vix_h_enter() -> f64 { 25.0 }
fn default_vix_h_exit() -> f64 { 22.0 }
fn default_vix_e_enter() -> f64 { 35.0 }
fn default_vix_e_exit() -> f64 { 30.0 }
fn default_garch_base() -> f64 { 0.80 }
fn default_cvar_mult() -> f64 { 1.5 }
fn default_re3_ic() -> f64 { 0.20 }
fn default_re3_trades() -> u32 { 20 }
fn default_re2_ic() -> f64 { 0.10 }
fn default_re2_trades() -> u32 { 10 }
fn default_macro_stale() -> u64 { 60 }
fn default_dd_vel_pct() -> f64 { 2.0 }
fn default_dd_vel_win() -> u64 { 3600 }
fn default_eq_snap_int() -> u64 { 60 }
fn default_eq_snap_ret() -> u64 { 7200 }
fn default_spread_edge() -> f64 { 2.0 }
fn default_scan_min() -> f64 { 30.0 }
fn default_kelly_floor() -> f64 { 0.005 }

#[derive(Debug, Deserialize)]
pub struct RawBrokerHardening {
    #[serde(default = "default_watchdog")] pub tick_watchdog_timeout_secs: u64,
    #[serde(default = "default_broker_esc")] pub broker_disconnect_escalate_secs: u64,
    #[serde(default = "default_zombie")] pub zombie_halt_timeout_mins: u64,
    #[serde(default = "default_cb_errors")] pub circuit_breaker_errors: u32,
    #[serde(default = "default_cb_window")] pub circuit_breaker_window_secs: u64,
    #[serde(default = "default_cb_cool")] pub circuit_breaker_cooldown_secs: u64,
    #[serde(default = "default_ack_timeout")] pub order_ack_timeout_secs: u64,
    #[serde(default = "default_fill_timeout")] pub order_fill_timeout_secs: u64,
    #[serde(default = "default_order_retries")] pub order_max_retries: u32,
    /// P3.6: Reconnect base backoff in milliseconds (exponential: base * 2^attempts).
    #[serde(default = "default_base_backoff_ms")] pub base_backoff_ms: u64,
    /// P3.6: Maximum backoff cap in milliseconds.
    #[serde(default = "default_max_backoff_ms")] pub max_backoff_ms: u64,
    /// P3.6: Jitter modulus in milliseconds (deterministic jitter = attempts * 137 % jitter_mod).
    #[serde(default = "default_jitter_mod_ms")] pub jitter_mod_ms: u64,
}

impl Default for RawBrokerHardening {
    fn default() -> Self {
        Self {
            tick_watchdog_timeout_secs: default_watchdog(),
            broker_disconnect_escalate_secs: default_broker_esc(),
            zombie_halt_timeout_mins: default_zombie(),
            circuit_breaker_errors: default_cb_errors(),
            circuit_breaker_window_secs: default_cb_window(),
            circuit_breaker_cooldown_secs: default_cb_cool(),
            order_ack_timeout_secs: default_ack_timeout(),
            order_fill_timeout_secs: default_fill_timeout(),
            order_max_retries: default_order_retries(),
            base_backoff_ms: default_base_backoff_ms(),
            max_backoff_ms: default_max_backoff_ms(),
            jitter_mod_ms: default_jitter_mod_ms(),
        }
    }
}

fn default_watchdog() -> u64 { 120 }
fn default_broker_esc() -> u64 { 10 }
fn default_zombie() -> u64 { 30 }
fn default_cb_errors() -> u32 { 5 }
fn default_cb_window() -> u64 { 60 }
fn default_cb_cool() -> u64 { 30 }
fn default_ack_timeout() -> u64 { 5 }
fn default_fill_timeout() -> u64 { 60 }
fn default_order_retries() -> u32 { 3 }
fn default_base_backoff_ms() -> u64 { 1_000 }
fn default_max_backoff_ms() -> u64 { 30_000 }
fn default_jitter_mod_ms() -> u64 { 1_000 }

#[derive(Debug, Deserialize)]
pub struct RawTickHardening {
    #[serde(default = "default_stale_tick_ms")] pub stale_tick_ms: u64,
    #[serde(default = "default_bar_ttl")] pub bar_history_ttl_secs: u64,
}

impl Default for RawTickHardening {
    fn default() -> Self {
        Self {
            stale_tick_ms: default_stale_tick_ms(),
            bar_history_ttl_secs: default_bar_ttl(),
        }
    }
}

fn default_stale_tick_ms() -> u64 { 500 }
fn default_bar_ttl() -> u64 { 3600 }

#[derive(Debug, Deserialize)]
pub struct RawSizingHardening {
    #[serde(default = "default_min_sim")] pub min_trade_gbp_sim: f64,
    #[serde(default = "default_min_live")] pub min_trade_gbp_live: f64,
    #[serde(default = "default_high_price")] pub high_price_guard_sim: f64,
    #[serde(default = "default_stop_min")] pub stop_pct_clamp_min: f64,
    #[serde(default = "default_stop_max")] pub stop_pct_clamp_max: f64,
    #[serde(default = "default_cold_stop")] pub cold_start_stop_pct: f64,
    #[serde(default = "default_kelly_floor_gbp")] pub kelly_notional_floor_gbp: f64,
    #[serde(default = "default_kelly_cap")] pub kelly_notional_cap_pct: f64,
}

impl Default for RawSizingHardening {
    fn default() -> Self {
        Self {
            min_trade_gbp_sim: default_min_sim(),
            min_trade_gbp_live: default_min_live(),
            high_price_guard_sim: default_high_price(),
            stop_pct_clamp_min: default_stop_min(),
            stop_pct_clamp_max: default_stop_max(),
            cold_start_stop_pct: default_cold_stop(),
            kelly_notional_floor_gbp: default_kelly_floor_gbp(),
            kelly_notional_cap_pct: default_kelly_cap(),
        }
    }
}

fn default_min_sim() -> f64 { 20.0 }
fn default_min_live() -> f64 { 1500.0 }
fn default_high_price() -> f64 { 2000.0 }
fn default_stop_min() -> f64 { 0.01 }
fn default_stop_max() -> f64 { 0.10 }
fn default_cold_stop() -> f64 { 0.05 }
fn default_kelly_floor_gbp() -> f64 { 100.0 }
fn default_kelly_cap() -> f64 { 0.25 }

// ── N8a: Live config overlay structs (all fields optional) ──

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawConfigLive {
    #[serde(default)]
    position: Option<RawPositionLive>,
    #[serde(default)]
    trading: Option<RawTradingLive>,
    #[serde(default)]
    signal: Option<RawSignalLive>,
    #[serde(default)]
    chandelier: Option<RawChandelierLive>,
    #[serde(default)]
    kelly: Option<RawKellyLive>,
    #[serde(default)]
    timing: Option<RawTimingLive>,
    #[serde(default)]
    risk: Option<RawRiskLive>,
    #[serde(default)]
    hardening: Option<RawHardeningLive>,
    #[serde(default)]
    entry_types: Option<RawEntryTypesLive>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawPositionLive {
    max_simultaneous_positions: Option<u32>,
    portfolio_heat_limit_pct: Option<f64>,
    sector_heat_cap_pct: Option<f64>,
    cash_buffer_pct: Option<f64>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawTradingLive {
    daily_trade_limit: Option<u32>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawSignalLive {
    confidence_floor: Option<f64>,
    min_edge_bps: Option<u32>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawChandelierLive {
    base_atr_mult: Option<f64>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawKellyLive {
    clamp_max: Option<f64>,
    clamp_min: Option<f64>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawTimingLive {
    stale_data_threshold_secs: Option<u64>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawRiskLive {
    max_daily_trades: Option<u32>,
    consecutive_loss_halt: Option<u32>,
    min_gross_edge_pct: Option<f64>,
    daily_drawdown_pct: Option<f64>,
    weekly_drawdown_pct: Option<f64>,
    peak_drawdown_halt_pct: Option<f64>,
    max_risk_per_trade_pct: Option<f64>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawHardeningSizingLive {
    min_trade_gbp_live: Option<f64>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawHardeningLive {
    system_velocity_max: Option<usize>,
    #[serde(default)]
    sizing: Option<RawHardeningSizingLive>,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
struct RawEntryTypesLive {
    type_a_confidence: Option<f64>,
    type_b_confidence: Option<f64>,
    type_c_confidence: Option<f64>,
    type_d_confidence: Option<f64>,
}

// ── Universe + Contracts ──

#[derive(Debug, Deserialize)]
struct RawUniverse {
    tickers: Vec<RawTicker>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct RawTicker {
    pub symbol: String,
    pub leverage: u8,
    pub underlying: String,
    pub sector: String,
    pub inverse_of: String,
}

#[derive(Debug, Deserialize)]
struct RawContracts {
    contracts: Vec<ContractEntry>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ContractEntry {
    pub symbol: String,
    pub con_id: i64,
    pub exchange: String,
    pub sec_type: String,
    pub currency: String,
    pub leverage: u8,
    pub sector: String,
    pub inverse_of: String,
}

#[derive(Debug, Deserialize)]
struct RawHolidays {
    holidays: HashMap<String, Vec<String>>,
}

// ── Engine configuration (assembled from all TOML files) ──

#[derive(Debug)]
pub struct EngineConfig {
    pub risk: RiskConfig,
    pub execution: ExecutionConfig,
    /// Q-051: Unified cost model — single source of truth for all trading costs.
    pub costs: CostsConfig,
    pub backpressure: BackpressureConfig,
    pub reconciliation: ReconciliationConfig,
    pub ibkr: IbkrConfig,
    pub rotation: RotationConfig,
    pub crucible: CrucibleConfig,
    pub inverse_pairs: Vec<[String; 2]>,
    pub sectors: HashMap<String, Vec<String>>,
    pub tickers: Vec<RawTicker>,
    pub contracts: Vec<ContractEntry>,
    pub holidays: Vec<String>,
    pub wal_schema_version: u8,
    pub gap_cooldown_mins: u32,
    pub slippage_pct: f64,
    /// Sprint 6: Chandelier exit ladder config.
    pub chandelier: RawChandelier,
    /// Sprint 6: Entry type base confidences and thresholds.
    pub entry_types: RawEntryTypes,
    /// Sprint 6: Risk arbiter hardening config.
    pub hardening: RawHardening,
    /// Sprint 7: Per-exchange entry cutoffs (exchange → "HH:MM" local time).
    pub exchange_cutoffs: HashMap<String, String>,
}

impl EngineConfig {
    /// Load all configuration from the given config directory.
    pub fn load(config_dir: &Path) -> Result<Self, ConfigError> {
        let raw = Self::load_config_toml(&config_dir.join("config.toml"))?;
        let tickers = Self::load_universe(&config_dir.join("initial_universe.toml"))?;
        let contracts = Self::load_contracts(&config_dir.join("contracts.toml"))?;
        let holidays = Self::load_holidays(&config_dir.join("uk_holidays.toml"))?;

        let risk = RiskConfig {
            max_positions: raw.position.max_simultaneous_positions,
            portfolio_heat_limit_pct: raw.position.portfolio_heat_limit_pct,
            sector_heat_cap_pct: raw.position.sector_heat_cap_pct,
            cash_buffer_pct: raw.position.cash_buffer_pct,
            daily_drawdown_pct: raw.risk.daily_drawdown_pct,
            spread_veto_pct: raw.risk.spread_veto_pct,
            stale_data_threshold_secs: raw.timing.stale_data_threshold_secs,
            confidence_floor: raw.signal.confidence_floor,
            entry_cutoff_secs: 20 * 3600 + 55 * 60, // 20:55 London (5 min before Dark at 21:00)
            auction_open_start_secs: 7 * 3600 + 50 * 60,
            auction_open_end_secs: 8 * 3600,
            auction_close_start_secs: 16 * 3600 + 30 * 60,
            auction_close_end_secs: 16 * 3600 + 35 * 60,
            velocity_window_ns: raw.signal.velocity_check_window_secs as u64 * 1_000_000_000,
            velocity_max_intents: raw.signal.velocity_check_max_intents,
            consecutive_loss_halt: raw.risk.consecutive_loss_halt,
            isa_annual_limit_gbp: raw.position.isa_annual_limit_gbp,
            minimum_entry_gbp: 1500.0,
            kelly_ramp_trades: 0,
            daily_trade_limit: raw.risk.max_daily_trades,
            min_gross_edge_pct: raw.risk.min_gross_edge_pct,
            // Sprint 6: Wire hardening config into RiskConfig
            system_velocity_max: raw.hardening.system_velocity_max,
            kelly_ramp_target: raw.hardening.kelly_ramp_target,
            kelly_ramp_clamp_min: raw.hardening.kelly_ramp_clamp_min,
            kelly_ramp_clamp_max: raw.hardening.kelly_ramp_clamp_max,
            vix_high_enter: raw.hardening.vix_high_enter,
            vix_high_exit: raw.hardening.vix_high_exit,
            vix_extreme_enter: raw.hardening.vix_extreme_enter,
            vix_extreme_exit: raw.hardening.vix_extreme_exit,
            garch_threshold_base: raw.hardening.garch_threshold_base,
            cvar_heat_multiplier: raw.hardening.cvar_heat_multiplier,
            reentry_3pos_ic: raw.hardening.reentry_3pos_ic,
            reentry_3pos_trades: raw.hardening.reentry_3pos_trades,
            reentry_2pos_ic: raw.hardening.reentry_2pos_ic,
            reentry_2pos_trades: raw.hardening.reentry_2pos_trades,
            macro_stress_stale_tick_secs: raw.hardening.macro_stress_stale_tick_secs,
            drawdown_velocity_pct: raw.hardening.drawdown_velocity_pct,
            drawdown_velocity_window_secs: raw.hardening.drawdown_velocity_window_secs,
            equity_snapshot_interval_secs: raw.hardening.equity_snapshot_interval_secs,
            equity_snapshot_retention_secs: raw.hardening.equity_snapshot_retention_secs,
            spread_edge_ratio: raw.hardening.spread_edge_ratio,
            scanner_score_min: raw.hardening.scanner_score_min,
            kelly_fraction_floor: raw.hardening.kelly_fraction_floor,
            // Sprint 10: Portfolio risk gates
            weekly_drawdown_pct: raw.risk.weekly_drawdown_pct,
            peak_drawdown_halt_pct: raw.risk.peak_drawdown_halt_pct,
            equity_floor_pct: raw.risk.equity_floor_pct,
            overnight_exposure_cap_pct: raw.risk.overnight_exposure_cap_pct,
            max_correlated_positions: raw.risk.max_correlated_positions,
            max_risk_per_trade_pct: raw.risk.max_risk_per_trade_pct,
            // R6: Dividend withholding from config (was hardcoded 0.85 in PortfolioState)
            dividend_withholding_factor: raw.position.dividend_withholding_factor,
        };

        Ok(EngineConfig {
            risk,
            execution: raw.execution,
            costs: raw.costs,
            backpressure: raw.backpressure,
            reconciliation: raw.reconciliation,
            ibkr: raw.ibkr,
            rotation: raw.rotation,
            crucible: raw.crucible,
            inverse_pairs: raw.inverse_pairs.pairs,
            sectors: raw.sectors,
            tickers,
            contracts,
            holidays,
            wal_schema_version: raw.wal.schema_version,
            gap_cooldown_mins: raw.timing.gap_cooldown_mins,
            slippage_pct: raw.risk.slippage_assumption_pct,
            chandelier: raw.chandelier,
            entry_types: raw.entry_types,
            hardening: raw.hardening,
            exchange_cutoffs: raw.timing.exchange_cutoffs,
        })
    }

    /// N8a: Load config with live overlay applied. When IS_LIVE=true, config.live.toml
    /// values override the base config.toml values for production-safe parameters.
    pub fn load_live(config_dir: &Path) -> Result<Self, ConfigError> {
        let mut cfg = Self::load(config_dir)?;
        let live_path = config_dir.join("config.live.toml");
        let live = Self::load_live_toml(&live_path)?;

        // Apply position overrides
        if let Some(pos) = live.position {
            if let Some(v) = pos.max_simultaneous_positions {
                cfg.risk.max_positions = v;
                cfg.crucible.max_positions_override = v;
            }
            if let Some(v) = pos.portfolio_heat_limit_pct { cfg.risk.portfolio_heat_limit_pct = v; }
            if let Some(v) = pos.sector_heat_cap_pct { cfg.risk.sector_heat_cap_pct = v; }
            if let Some(v) = pos.cash_buffer_pct { cfg.risk.cash_buffer_pct = v; }
        }

        // Apply trading overrides
        if let Some(trading) = live.trading {
            if let Some(v) = trading.daily_trade_limit { cfg.risk.daily_trade_limit = v; }
        }

        // Apply signal overrides
        if let Some(signal) = live.signal {
            if let Some(v) = signal.confidence_floor { cfg.risk.confidence_floor = v; }
            if let Some(v) = signal.min_edge_bps {
                cfg.risk.min_gross_edge_pct = v as f64 / 100.0; // bps → pct
            }
        }

        // Apply kelly overrides (stored in slippage_pct field for now — N8a wires to risk)
        // Note: Kelly params are in dynamic_weights.toml, not config.toml.
        // config.live.toml kelly section is for documentation/assertion only.

        // C2: Apply timing overrides
        if let Some(timing) = live.timing {
            if let Some(v) = timing.stale_data_threshold_secs { cfg.risk.stale_data_threshold_secs = v; }
        }

        // C2: Apply risk overrides (uses same field names as config.toml [risk] section)
        if let Some(risk) = live.risk {
            if let Some(v) = risk.max_daily_trades { cfg.risk.daily_trade_limit = v; }
            if let Some(v) = risk.consecutive_loss_halt { cfg.risk.consecutive_loss_halt = v; }
            if let Some(v) = risk.min_gross_edge_pct { cfg.risk.min_gross_edge_pct = v; }
            if let Some(v) = risk.daily_drawdown_pct { cfg.risk.daily_drawdown_pct = v; }
            if let Some(v) = risk.weekly_drawdown_pct { cfg.risk.weekly_drawdown_pct = v; }
            if let Some(v) = risk.peak_drawdown_halt_pct { cfg.risk.peak_drawdown_halt_pct = v; }
            if let Some(v) = risk.max_risk_per_trade_pct { cfg.risk.max_risk_per_trade_pct = v; }
        }

        // C2: Apply hardening overrides
        if let Some(hardening) = live.hardening {
            if let Some(v) = hardening.system_velocity_max { cfg.risk.system_velocity_max = v; }
            if let Some(sizing) = hardening.sizing {
                if let Some(v) = sizing.min_trade_gbp_live { cfg.hardening.sizing.min_trade_gbp_live = v; }
            }
        }

        // C2: Apply entry_types overrides
        if let Some(et) = live.entry_types {
            if let Some(v) = et.type_a_confidence { cfg.entry_types.type_a_confidence = v; }
            if let Some(v) = et.type_b_confidence { cfg.entry_types.type_b_confidence = v; }
            if let Some(v) = et.type_c_confidence { cfg.entry_types.type_c_confidence = v; }
            if let Some(v) = et.type_d_confidence { cfg.entry_types.type_d_confidence = v; }
        }

        eprintln!(
            "N8a LIVE OVERLAY: max_pos={}, heat={:.1}%, sector={:.1}%, buffer={:.1}%, trades/day={}, consecutive_halt={}, edge={:.2}%",
            cfg.risk.max_positions,
            cfg.risk.portfolio_heat_limit_pct,
            cfg.risk.sector_heat_cap_pct,
            cfg.risk.cash_buffer_pct,
            cfg.risk.daily_trade_limit,
            cfg.risk.consecutive_loss_halt,
            cfg.risk.min_gross_edge_pct,
        );

        Ok(cfg)
    }

    fn load_config_toml(path: &Path) -> Result<RawConfig, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        toml::from_str(&content).map_err(|e| ConfigError::Parse(format!("{path:?}: {e}")))
    }

    fn load_live_toml(path: &Path) -> Result<RawConfigLive, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        toml::from_str(&content).map_err(|e| ConfigError::Parse(format!("{path:?}: {e}")))
    }

    fn load_universe(path: &Path) -> Result<Vec<RawTicker>, ConfigError> {
        // Try active_watchlist.json first (daily-generated by ticker_selector).
        // Falls back to initial_universe.toml (seed file for cold start).
        let watchlist_path = path.with_file_name("active_watchlist.json");
        if watchlist_path.exists() {
            if let Ok(tickers) = Self::load_universe_from_watchlist(&watchlist_path) {
                if !tickers.is_empty() {
                    eprintln!(
                        "CONFIG: Loaded {} tickers from active_watchlist.json (daily-ranked)",
                        tickers.len()
                    );
                    return Ok(tickers);
                }
            }
            eprintln!("CONFIG: active_watchlist.json exists but failed to parse, falling back to TOML");
        }

        let content = std::fs::read_to_string(path)?;
        let raw: RawUniverse =
            toml::from_str(&content).map_err(|e| ConfigError::Parse(format!("{path:?}: {e}")))?;
        eprintln!(
            "CONFIG: Loaded {} tickers from initial_universe.toml (seed file)",
            raw.tickers.len()
        );
        Ok(raw.tickers)
    }

    /// Load tickers from active_watchlist.json (generated by ticker_selector.py daily).
    pub fn load_universe_from_watchlist(path: &Path) -> Result<Vec<RawTicker>, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        let json: serde_json::Value =
            serde_json::from_str(&content).map_err(|e| ConfigError::Parse(format!("{path:?}: {e}")))?;

        let mut tickers = Vec::new();

        // Extract vanguard tickers (top 200 — real-time monitoring tier)
        if let Some(vanguard) = json.get("vanguard").and_then(|v| v.as_array()) {
            for entry in vanguard {
                if let Some(ticker) = Self::json_entry_to_raw_ticker(entry) {
                    tickers.push(ticker);
                }
            }
        }

        Ok(tickers)
    }

    /// Convert a single JSON ticker entry from active_watchlist.json to RawTicker.
    pub fn json_entry_to_raw_ticker(entry: &serde_json::Value) -> Option<RawTicker> {
        let symbol = entry.get("symbol")?.as_str()?.to_string();
        if symbol.is_empty() {
            return None;
        }
        let leverage = entry
            .get("leverage_factor")
            .and_then(|v| v.as_u64())
            .unwrap_or(1) as u8;
        let sector = entry
            .get("sector")
            .and_then(|v| v.as_str())
            .unwrap_or("Unknown")
            .to_string();
        let name = entry
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let inverse = entry
            .get("inverse")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);

        Some(RawTicker {
            symbol,
            leverage,
            underlying: name,
            sector,
            inverse_of: if inverse { "yes".to_string() } else { String::new() },
        })
    }

    fn load_contracts(path: &Path) -> Result<Vec<ContractEntry>, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        let raw: RawContracts =
            toml::from_str(&content).map_err(|e| ConfigError::Parse(format!("{path:?}: {e}")))?;
        let mut contracts = raw.contracts;
        for contract in &mut contracts {
            if contract.leverage < 1 || contract.leverage > 10 {
                eprintln!(
                    "WARN: contract {} has invalid leverage {}, clamping to [1,10]",
                    contract.symbol, contract.leverage
                );
                contract.leverage = contract.leverage.max(1).min(10);
            }
            if contract.leverage > 5 {
                eprintln!(
                    "INFO: contract {} has high leverage {}",
                    contract.symbol, contract.leverage
                );
            }
        }
        Ok(contracts)
    }

    /// Load contracts from a standalone path (for hot-reload via SIGHUP).
    pub fn load_contracts_standalone(path: &Path) -> Result<Vec<ContractEntry>, ConfigError> {
        Self::load_contracts(path)
    }

    fn load_holidays(path: &Path) -> Result<Vec<String>, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        let raw: RawHolidays =
            toml::from_str(&content).map_err(|e| ConfigError::Parse(format!("{path:?}: {e}")))?;
        let mut all_holidays = Vec::new();
        for dates in raw.holidays.values() {
            all_holidays.extend(dates.iter().cloned());
        }
        all_holidays.sort();
        Ok(all_holidays)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_load_full_config() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        assert_eq!(cfg.risk.max_positions, 15);  // PAPER VALIDATION: maximise trade data
        assert_eq!(cfg.risk.confidence_floor, 65.0);  // N0c: raised from 45
        assert_eq!(cfg.risk.daily_drawdown_pct, 4.0);
        assert_eq!(cfg.risk.isa_annual_limit_gbp, 20_000.0);
        assert!(cfg.crucible.paper_mode);
        assert_eq!(cfg.crucible.starting_equity_gbp, 10_000.0);
        assert_eq!(cfg.crucible.max_positions_override, 15); // PAPER VALIDATION: maximise trade data
    }

    #[test]
    fn test_contracts_loaded() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        // Expanded: 12 LSE + 60 TSE + 40 HKEX + 49 KRX + 20 XETRA + 15 EURONEXT + 70 US + 10 SGX + auto-expanded
        assert!(cfg.contracts.len() >= 200, "Expected >= 200 contracts, got {}", cfg.contracts.len());
        let qqq3 = cfg
            .contracts
            .iter()
            .find(|c| c.symbol == "QQQ3.L")
            .expect("QQQ3.L");
        assert_eq!(qqq3.leverage, 3);
        assert_eq!(qqq3.sector, "Technology");
        assert_eq!(qqq3.exchange, "LSEETF");
    }

    #[test]
    fn test_universe_loaded() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        // Must have tickers from whatever exchanges are currently open.
        // When LSE is closed, Core 12 are absent — this is correct.
        assert!(!cfg.tickers.is_empty(), "Universe must not be empty");
        // At least some tickers should be loaded from initial_universe.toml
        assert!(cfg.tickers.len() >= 10, "Expected >= 10 tickers, got {}", cfg.tickers.len());
    }

    #[test]
    fn test_holidays_loaded() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        assert!(cfg.holidays.contains(&"2026-01-01".to_string()));
        assert!(cfg.holidays.contains(&"2026-12-25".to_string()));
    }

    #[test]
    fn test_inverse_pairs() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        assert_eq!(cfg.inverse_pairs.len(), 2);
        assert_eq!(cfg.inverse_pairs[0], ["QQQ3.L", "QQQS.L"]);
    }

    #[test]
    fn test_sectors() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        let tech = cfg.sectors.get("Technology").expect("Technology sector");
        assert!(tech.contains(&"QQQ3.L".to_string()));
    }

    #[test]
    fn test_execution_config() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        assert!((cfg.execution.marketable_limit_buffer_pct - 0.1).abs() < 0.001);
        assert!((cfg.execution.tick_size_under_1 - 0.001).abs() < 0.0001);
        assert!((cfg.execution.tick_size_over_1 - 0.01).abs() < 0.001);
    }

    #[test]
    fn test_ibkr_config() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load(&config_dir).expect("load config");
        assert_eq!(cfg.ibkr.client_id_executioner, 101);
        assert_eq!(cfg.ibkr.rate_limit_msgs_per_sec, 50);
        assert_eq!(cfg.ibkr.reqmktdata_pacing_ms, 10);
        assert_eq!(cfg.ibkr.max_simultaneous_lines, 100);
    }

    // ── N8a: Live overlay tests ──

    #[test]
    fn test_live_config_parses() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let live_path = config_dir.join("config.live.toml");
        let live: RawConfigLive = {
            let content = std::fs::read_to_string(&live_path).expect("read config.live.toml");
            toml::from_str(&content).expect("parse config.live.toml")
        };
        // Verify key sections exist
        assert!(live.position.is_some(), "config.live.toml must have [position]");
        let pos = live.position.unwrap();
        assert_eq!(pos.max_simultaneous_positions, Some(3));
        assert_eq!(pos.portfolio_heat_limit_pct, Some(10.0));
        assert_eq!(pos.sector_heat_cap_pct, Some(33.0));
        assert_eq!(pos.cash_buffer_pct, Some(25.0));
    }

    #[test]
    fn test_live_overlay_applies() {
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        // Base config has paper values
        let base = EngineConfig::load(&config_dir).expect("load base");
        assert_eq!(base.risk.max_positions, 15); // Paper: relaxed

        // Live overlay should tighten
        let live = EngineConfig::load_live(&config_dir).expect("load live");
        assert_eq!(live.risk.max_positions, 3); // Live: tightened
        assert_eq!(live.risk.portfolio_heat_limit_pct, 10.0);
        assert_eq!(live.risk.sector_heat_cap_pct, 33.0);
        assert_eq!(live.risk.cash_buffer_pct, 25.0);
    }

    #[test]
    fn test_n8b_live_config_values_safe() {
        // N8b: Assert config.live.toml values meet production safety criteria
        let config_dir = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent dir")
            .join("config");
        let cfg = EngineConfig::load_live(&config_dir).expect("load live");

        // Position limits: must be conservative for £10k equity
        assert!(cfg.risk.max_positions <= 5, "Live: max_positions={} exceeds 5", cfg.risk.max_positions);
        assert!(cfg.risk.portfolio_heat_limit_pct <= 20.0, "Live: heat={:.1}% exceeds 20%", cfg.risk.portfolio_heat_limit_pct);
        assert!(cfg.risk.sector_heat_cap_pct <= 50.0, "Live: sector_heat={:.1}% exceeds 50%", cfg.risk.sector_heat_cap_pct);
        assert!(cfg.risk.cash_buffer_pct >= 15.0, "Live: cash_buffer={:.1}% below 15%", cfg.risk.cash_buffer_pct);

        // Trade limits: must have daily cap
        assert!(cfg.risk.daily_trade_limit <= 5, "Live: daily_trades={} exceeds 5", cfg.risk.daily_trade_limit);

        // Signal quality: must maintain confidence floor
        assert!(cfg.risk.confidence_floor >= 60.0, "Live: confidence_floor={:.0} below 60", cfg.risk.confidence_floor);
    }
}

// ── P1-2.15: Economic Calendar ──

/// Parsed economic calendar event with UTC timestamps.
#[derive(Clone, Debug)]
pub struct CalendarEvent {
    pub name: String,
    /// Event time as seconds from midnight UTC.
    pub date: String,
    pub time_secs: u32,
    /// Window in minutes before and after event.
    pub window_mins: u32,
}

/// Load economic calendar from TOML.
pub fn load_economic_calendar(config_dir: &Path) -> Vec<CalendarEvent> {
    let path = config_dir.join("economic_calendar.toml");
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("ECON_CALENDAR: No calendar file at {}: {e}", path.display());
            return Vec::new();
        }
    };

    #[derive(Deserialize)]
    struct RawCalendar {
        window_minutes: Option<u32>,
        events: Option<Vec<RawEvent>>,
    }
    #[derive(Deserialize)]
    struct RawEvent {
        name: String,
        datetime: String,
    }

    let raw: RawCalendar = match toml::from_str(&content) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("ECON_CALENDAR: Parse error: {e}");
            return Vec::new();
        }
    };

    let window = raw.window_minutes.unwrap_or(15);
    let mut events = Vec::new();

    for ev in raw.events.unwrap_or_default() {
        // Parse "YYYY-MM-DD HH:MM" → (date, secs_from_midnight)
        let parts: Vec<&str> = ev.datetime.split(' ').collect();
        if parts.len() != 2 {
            continue;
        }
        let date = parts[0].to_string();
        let time_parts: Vec<&str> = parts[1].split(':').collect();
        if time_parts.len() != 2 {
            continue;
        }
        let hours: u32 = time_parts[0].parse().unwrap_or(0);
        let mins: u32 = time_parts[1].parse().unwrap_or(0);
        let time_secs = hours * 3600 + mins * 60;

        events.push(CalendarEvent {
            name: ev.name,
            date,
            time_secs,
            window_mins: window,
        });
    }

    eprintln!("ECON_CALENDAR: Loaded {} events (window={}min)", events.len(), window);
    events
}
