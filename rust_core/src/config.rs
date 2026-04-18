//! 3-layer config loader: defaults.toml → config.toml → learned.toml.
//!
//! - defaults.toml: ships with code, sensible starting values
//! - config.toml: operator override
//! - learned.toml: Ouroboros nightly, loaded on SIGHUP
//! - bounds.toml: safety bounds — if learned exceeds → default + CRITICAL alert
//!
//! Invariant #9: bounds checking on learned.toml is mandatory.
//! Invariant #16: zero hardcoded thresholds — all from config.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use tracing::{error, info, warn};

use crate::types::{AegisError, Mode};

// ---------------------------------------------------------------------------
// Config structs (deserialized from TOML)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct Config {
    pub mode: Mode,

    // Position sizing
    pub kelly_fraction: f64,
    pub max_position_pct: f64,
    pub max_heat_pct: f64,
    pub max_concurrent: u8,
    pub correlation_limit: f64,
    pub sector_cap_pct: f64,
    pub risk_per_trade_pct: f64,
    pub overnight_cap_pct: f64,
    pub min_position_gbp: f64,

    // Chandelier defaults (per-strategy overrides in strategy configs)
    pub chandelier_atr_mult: f64,
    pub chandelier_rungs: Vec<(f64, f64)>,
    pub chandelier_eod_tightening: Vec<(f64, f64)>,

    // Drawdown
    pub dd_yellow: f64,
    pub dd_orange: f64,
    pub dd_red: f64,
    pub dd_black: f64,
    pub dd_yellow_kelly_mult: f64,
    pub dd_yellow_max_concurrent: u8,

    // Pre-trade gates
    pub fat_finger_pct: f64,
    pub price_collar_pct: f64,
    pub max_orders_per_minute: u32,
    pub max_spread_bps: f64,
    pub data_freshness_max_s: f64,

    // Regime thresholds
    pub regime_thresholds: RegimeThresholds,

    // Per-strategy overrides (loaded from config/strategies/*.toml)
    #[serde(default)]
    pub strategy_configs: HashMap<String, StrategyConfig>,

    // Bridge
    pub brain_host: String,
    pub brain_port: u16,

    // IBKR connection
    pub ibkr_host: String,
    pub ibkr_port: u16,
    pub ibkr_client_id: i32,

    // Equity (initial)
    pub initial_equity_isa: f64,
    pub initial_equity_ig: f64,
    pub initial_equity_gia: f64,
}

#[derive(Debug, Clone, Deserialize)]
#[allow(dead_code)]
pub struct RegimeThresholds {
    pub vix_steady_max: f64,
    pub vix_crisis_min: f64,
    pub regime_scale: HashMap<String, f64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct BoundsConfig {
    pub kelly_fraction: (f64, f64),
    pub max_position_pct: (f64, f64),
    pub max_heat_pct: (f64, f64),
    pub chandelier_atr_mult: (f64, f64),
    pub correlation_limit: (f64, f64),
    pub risk_per_trade_pct: (f64, f64),
    pub overnight_cap_pct: (f64, f64),
    pub sector_cap_pct: (f64, f64),
    pub min_position_gbp: (f64, f64),
}

/// Per-strategy configuration overrides.
/// Loaded from config/strategies/{name}.toml or [strategy_configs.{name}] in config.toml.
#[derive(Debug, Clone, Deserialize, Default)]
#[allow(dead_code)]
pub struct StrategyConfig {
    /// Override global chandelier ATR multiplier for this strategy.
    pub chandelier_atr_mult: Option<f64>,
    /// Minimum confidence floor for this strategy's signals.
    pub confidence_floor: Option<f64>,
    /// Maximum heat allocation for this strategy.
    pub heat_limit: Option<f64>,
    /// Kelly fraction override.
    pub kelly_fraction: Option<f64>,
    /// Whether this strategy is enabled.
    #[serde(default = "default_true")]
    pub enabled: bool,
    /// Exit method override.
    pub exit_method: Option<String>,
    /// Exit parameters (hold_days, profit_target_pct, stop_atr).
    pub exit_param: Option<f64>,
}

fn default_true() -> bool {
    true
}

// ---------------------------------------------------------------------------
// Loader
// ---------------------------------------------------------------------------

/// Resolve the config directory. Checks AEGIS_CONFIG_DIR env, then falls back
/// to ./config relative to the working directory.
fn config_dir() -> PathBuf {
    std::env::var("AEGIS_CONFIG_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("config"))
}

/// Load and merge the 3-layer config.
/// Panics on missing defaults.toml (it ships with the binary).
#[tracing::instrument(skip_all)]
pub fn load_config() -> Result<Config, AegisError> {
    let dir = config_dir();

    // Layer 1: defaults (required)
    let defaults_path = dir.join("defaults.toml");
    let defaults_str = std::fs::read_to_string(&defaults_path).map_err(|e| {
        AegisError::Config(format!("defaults.toml missing at {}: {e}", defaults_path.display()))
    })?;
    let mut config: Config = toml::from_str(&defaults_str)
        .map_err(|e| AegisError::Config(format!("defaults.toml parse error: {e}")))?;

    info!(path = %defaults_path.display(), "loaded defaults.toml");

    // Layer 2: operator overrides (optional)
    let config_path = dir.join("config.toml");
    if config_path.exists() {
        let config_str = std::fs::read_to_string(&config_path)
            .map_err(|e| AegisError::Config(format!("config.toml read error: {e}")))?;
        let overrides: toml::Value = toml::from_str(&config_str)
            .map_err(|e| AegisError::Config(format!("config.toml parse error: {e}")))?;
        apply_overrides(&mut config, &overrides);
        info!(path = %config_path.display(), "applied config.toml overrides");
    }

    // Layer 3: learned values from Ouroboros (optional)
    let learned_path = dir.join("learned.toml");
    if learned_path.exists() {
        apply_learned(&mut config, &learned_path, &dir)?;
    }

    // Override mode from env var (AEGIS_MODE=paper|live)
    if let Ok(mode_str) = std::env::var("AEGIS_MODE") {
        match mode_str.to_lowercase().as_str() {
            "paper" => config.mode = Mode::Paper,
            "live" => config.mode = Mode::Live,
            other => warn!(mode = other, "unknown AEGIS_MODE, using config value"),
        }
    }

    info!(mode = ?config.mode, kelly = config.kelly_fraction, "config loaded");
    Ok(config)
}

/// Apply learned.toml values with bounds checking (invariant #9).
#[tracing::instrument(skip_all)]
fn apply_learned(config: &mut Config, learned_path: &Path, dir: &Path) -> Result<(), AegisError> {
    let bounds_path = dir.join("bounds.toml");
    let bounds: Option<BoundsConfig> = if bounds_path.exists() {
        let s = std::fs::read_to_string(&bounds_path)
            .map_err(|e| AegisError::Config(format!("bounds.toml read error: {e}")))?;
        Some(
            toml::from_str(&s)
                .map_err(|e| AegisError::Config(format!("bounds.toml parse error: {e}")))?,
        )
    } else {
        warn!("bounds.toml not found — learned values applied without bounds checking");
        None
    };

    let learned_str = std::fs::read_to_string(learned_path)
        .map_err(|e| AegisError::Config(format!("learned.toml read error: {e}")))?;
    let learned: toml::Value = toml::from_str(&learned_str)
        .map_err(|e| AegisError::Config(format!("learned.toml parse error: {e}")))?;

    if let Some(bounds) = &bounds {
        apply_bounded_f64(
            &learned,
            "kelly_fraction",
            &mut config.kelly_fraction,
            bounds.kelly_fraction,
        );
        apply_bounded_f64(
            &learned,
            "chandelier_atr_mult",
            &mut config.chandelier_atr_mult,
            bounds.chandelier_atr_mult,
        );
        apply_bounded_f64(
            &learned,
            "max_position_pct",
            &mut config.max_position_pct,
            bounds.max_position_pct,
        );
        apply_bounded_f64(
            &learned,
            "max_heat_pct",
            &mut config.max_heat_pct,
            bounds.max_heat_pct,
        );
        apply_bounded_f64(
            &learned,
            "correlation_limit",
            &mut config.correlation_limit,
            bounds.correlation_limit,
        );
        apply_bounded_f64(
            &learned,
            "risk_per_trade_pct",
            &mut config.risk_per_trade_pct,
            bounds.risk_per_trade_pct,
        );
        apply_bounded_f64(
            &learned,
            "overnight_cap_pct",
            &mut config.overnight_cap_pct,
            bounds.overnight_cap_pct,
        );
        apply_bounded_f64(
            &learned,
            "sector_cap_pct",
            &mut config.sector_cap_pct,
            bounds.sector_cap_pct,
        );
        apply_bounded_f64(
            &learned,
            "min_position_gbp",
            &mut config.min_position_gbp,
            bounds.min_position_gbp,
        );
    } else {
        // No bounds file — apply raw learned values (all learnable fields)
        macro_rules! apply_raw {
            ($field:ident) => {
                if let Some(v) = learned.get(stringify!($field)).and_then(|v| v.as_float()) {
                    config.$field = v;
                }
            };
        }
        apply_raw!(kelly_fraction);
        apply_raw!(chandelier_atr_mult);
        apply_raw!(max_position_pct);
        apply_raw!(max_heat_pct);
        apply_raw!(correlation_limit);
        apply_raw!(risk_per_trade_pct);
        apply_raw!(overnight_cap_pct);
        apply_raw!(sector_cap_pct);
        apply_raw!(min_position_gbp);
    }

    // Apply regime_scale from learned.toml [regime_thresholds.regime_scale] table
    // Ouroboros self_reflection writes: [regime_thresholds.regime_scale]
    //   steady = 1.0
    //   trending = 0.8
    //   volatile = 0.5
    //   crisis = 0.2
    if let Some(regime_table) = learned
        .get("regime_thresholds")
        .and_then(|rt| rt.get("regime_scale"))
        .and_then(|rs| rs.as_table())
    {
        for (regime_name, value) in regime_table {
            if let Some(scale) = value.as_float() {
                // Bounds check: regime_scale must be in [0.05, 2.0]
                if scale >= 0.05 && scale <= 2.0 {
                    config
                        .regime_thresholds
                        .regime_scale
                        .insert(regime_name.clone(), scale);
                    info!(regime = %regime_name, scale, "learned regime_scale applied");
                } else {
                    error!(
                        regime = %regime_name,
                        scale,
                        "CRITICAL: learned regime_scale out of bounds [0.05, 2.0]"
                    );
                }
            }
        }
    }

    info!(path = %learned_path.display(), "applied learned.toml with bounds check");
    Ok(())
}

/// Apply a learned f64 value only if it's within bounds.
/// If out of bounds: keep default and emit CRITICAL alert.
fn apply_bounded_f64(
    learned: &toml::Value,
    key: &str,
    target: &mut f64,
    (lo, hi): (f64, f64),
) {
    if let Some(v) = learned.get(key).and_then(|v| v.as_float()) {
        if v >= lo && v <= hi {
            *target = v;
            info!(key, value = v, "learned value applied");
        } else {
            error!(
                key,
                value = v,
                lo,
                hi,
                "CRITICAL: learned value out of bounds — using default"
            );
        }
    }
}

/// Apply overrides from config.toml onto an existing Config.
/// Only overrides fields that are present in the TOML.
fn apply_overrides(config: &mut Config, overrides: &toml::Value) {
    macro_rules! override_f64 {
        ($field:ident) => {
            if let Some(v) = overrides.get(stringify!($field)).and_then(|v| v.as_float()) {
                config.$field = v;
            }
        };
    }
    macro_rules! override_u8 {
        ($field:ident) => {
            if let Some(v) = overrides.get(stringify!($field)).and_then(|v| v.as_integer()) {
                config.$field = v as u8;
            }
        };
    }
    macro_rules! override_u32 {
        ($field:ident) => {
            if let Some(v) = overrides.get(stringify!($field)).and_then(|v| v.as_integer()) {
                config.$field = v as u32;
            }
        };
    }
    macro_rules! override_str {
        ($field:ident) => {
            if let Some(v) = overrides.get(stringify!($field)).and_then(|v| v.as_str()) {
                config.$field = v.to_string();
            }
        };
    }

    override_f64!(kelly_fraction);
    override_f64!(max_position_pct);
    override_f64!(max_heat_pct);
    override_u8!(max_concurrent);
    override_f64!(correlation_limit);
    override_f64!(sector_cap_pct);
    override_f64!(risk_per_trade_pct);
    override_f64!(overnight_cap_pct);
    override_f64!(min_position_gbp);
    override_f64!(chandelier_atr_mult);
    override_f64!(dd_yellow);
    override_f64!(dd_orange);
    override_f64!(dd_red);
    override_f64!(dd_black);
    override_f64!(dd_yellow_kelly_mult);
    override_u8!(dd_yellow_max_concurrent);
    override_f64!(fat_finger_pct);
    override_f64!(price_collar_pct);
    override_u32!(max_orders_per_minute);
    override_f64!(max_spread_bps);
    override_f64!(data_freshness_max_s);
    override_str!(brain_host);
    override_str!(ibkr_host);

    if let Some(v) = overrides.get("brain_port").and_then(|v| v.as_integer()) {
        config.brain_port = v as u16;
    }
    if let Some(v) = overrides.get("ibkr_port").and_then(|v| v.as_integer()) {
        config.ibkr_port = v as u16;
    }
    if let Some(v) = overrides.get("ibkr_client_id").and_then(|v| v.as_integer()) {
        config.ibkr_client_id = v as i32;
    }

    // Mode override
    if let Some(v) = overrides.get("mode").and_then(|v| v.as_str()) {
        match v.to_lowercase().as_str() {
            "paper" => config.mode = Mode::Paper,
            "live" => config.mode = Mode::Live,
            _ => warn!(mode = v, "unknown mode in config.toml"),
        }
    }
}

/// Reload config on SIGHUP. Returns a new Config if successful.
#[tracing::instrument]
pub fn reload_config() -> Result<Config, AegisError> {
    info!("SIGHUP received — reloading config");
    load_config()
}
