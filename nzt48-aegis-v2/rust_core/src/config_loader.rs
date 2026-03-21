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
}

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
}

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
}

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

        eprintln!(
            "N8a LIVE OVERLAY: max_pos={}, heat={:.1}%, sector={:.1}%, buffer={:.1}%, trades/day={}",
            cfg.risk.max_positions,
            cfg.risk.portfolio_heat_limit_pct,
            cfg.risk.sector_heat_cap_pct,
            cfg.risk.cash_buffer_pct,
            cfg.risk.daily_trade_limit,
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
        Ok(raw.contracts)
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
