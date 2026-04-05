//! Morning boot loader for Ouroboros nightly artifacts.
//! Loads dynamic_weights.toml and universe_classification.toml atomically.
//! Safe fallback: if loading fails, returns defaults (yesterday's values).

use serde::Deserialize;
use std::collections::HashMap;
use std::path::Path;

/// Dynamic weights produced by Ouroboros nightly pipeline.
#[derive(Debug, Clone)]
pub struct DynamicWeights {
    pub bayesian_win_rate: f64,
    pub trade_count: u32,
    pub sharpe_ratio: f64,
    pub dsr: f64,
    pub dsr_significant: bool,
    pub chandelier_atr_mult: f64,
    pub regime_best: String,
    pub regime_worst: String,
    pub regime_scales: HashMap<String, f64>,
    pub kelly_fractions: HashMap<String, f64>,
    /// Tickers blacklisted by Ouroboros (WR < 30% over 10+ trades).
    /// Engine should reject signals for these tickers.
    pub ticker_blacklist: Vec<String>,
}

impl Default for DynamicWeights {
    fn default() -> Self {
        Self {
            bayesian_win_rate: 0.5,
            trade_count: 0,
            sharpe_ratio: 0.0,
            dsr: 0.0,
            dsr_significant: false,
            chandelier_atr_mult: 3.0,
            regime_best: "bull_quiet".to_string(),
            regime_worst: "bear_volatile".to_string(),
            regime_scales: HashMap::new(),
            kelly_fractions: HashMap::new(),
            ticker_blacklist: Vec::new(),
        }
    }
}

/// Universe tier classification produced by Ouroboros.
#[derive(Debug, Clone, Default)]
pub struct UniverseClassification {
    pub tier1: Vec<i64>,
    pub tier2: Vec<i64>,
    pub tier3: Vec<i64>,
    pub locked: Vec<i64>,
}

// TOML deserialization intermediaries
#[derive(Deserialize)]
struct RawDynamicWeights {
    #[allow(dead_code)]
    schema_version: u8,
    bayesian: RawBayesian,
    exit: RawExit,
    regime: RawRegime,
    kelly_fractions: Option<HashMap<String, f64>>,
    #[serde(default)]
    ticker_blacklist: Option<RawTickerBlacklist>,
    #[serde(default)]
    #[allow(dead_code)]
    signal: Option<RawSignal>,
}

#[derive(Deserialize, Default)]
struct RawTickerBlacklist {
    #[serde(default)]
    tickers: Vec<String>,
}

#[derive(Deserialize, Default)]
struct RawSignal {
    #[serde(default)]
    #[allow(dead_code)]
    confidence_floor: Option<u32>,
}

#[derive(Deserialize)]
struct RawBayesian {
    win_rate: f64,
    trade_count: u32,
    sharpe_ratio: f64,
    dsr: f64,
    dsr_significant: bool,
}

#[derive(Deserialize)]
struct RawExit {
    chandelier_atr_mult: f64,
    #[allow(dead_code)]
    rung5_rate: f64,
}

#[derive(Deserialize)]
struct RawRegime {
    best: String,
    worst: String,
    #[serde(flatten)]
    scales: HashMap<String, toml::Value>,
}

#[derive(Deserialize)]
struct RawTiers {
    tier1: Vec<i64>,
    tier2: Vec<i64>,
    tier3: Vec<i64>,
    locked: Vec<i64>,
}

#[derive(Deserialize)]
struct RawUniverseClass {
    #[allow(dead_code)]
    schema_version: u8,
    tiers: RawTiers,
}

/// Load dynamic_weights.toml with safe fallback.
/// Returns defaults if file doesn't exist or is malformed.
pub fn load_dynamic_weights(config_dir: &Path) -> DynamicWeights {
    let path = config_dir.join("dynamic_weights.toml");
    _load_dw(&path).unwrap_or_default()
}

fn _load_dw(path: &Path) -> Result<DynamicWeights, String> {
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let raw: RawDynamicWeights = toml::from_str(&content).map_err(|e| e.to_string())?;

    let mut regime_scales = HashMap::new();
    for (k, v) in &raw.regime.scales {
        if k != "best"
            && k != "worst"
            && let Some(f) = v.as_float()
        {
            regime_scales.insert(k.clone(), f);
        }
    }

    let blacklist = raw.ticker_blacklist
        .map(|b| b.tickers)
        .unwrap_or_default();
    if !blacklist.is_empty() {
        eprintln!("OUROBOROS: ticker blacklist loaded: {:?}", blacklist);
    }

    Ok(DynamicWeights {
        bayesian_win_rate: raw.bayesian.win_rate,
        trade_count: raw.bayesian.trade_count,
        sharpe_ratio: raw.bayesian.sharpe_ratio,
        dsr: raw.bayesian.dsr,
        dsr_significant: raw.bayesian.dsr_significant,
        chandelier_atr_mult: raw.exit.chandelier_atr_mult,
        regime_best: raw.regime.best,
        regime_worst: raw.regime.worst,
        regime_scales,
        kelly_fractions: raw.kelly_fractions.unwrap_or_default(),
        ticker_blacklist: blacklist,
    })
}

/// Load universe_classification.toml with safe fallback.
/// Returns defaults if file doesn't exist or is malformed.
pub fn load_universe_classification(config_dir: &Path) -> UniverseClassification {
    let path = config_dir.join("universe_classification.toml");
    _load_uc(&path).unwrap_or_default()
}

fn _load_uc(path: &Path) -> Result<UniverseClassification, String> {
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let raw: RawUniverseClass = toml::from_str(&content).map_err(|e| e.to_string())?;
    Ok(UniverseClassification {
        tier1: raw.tiers.tier1,
        tier2: raw.tiers.tier2,
        tier3: raw.tiers.tier3,
        locked: raw.tiers.locked,
    })
}

/// Phase 16: Spread cache from Ouroboros nightly (5-day median intraday spreads).
/// Used by Smart Router for cost comparison (v22-FIX-2).
#[derive(Debug, Clone, Default)]
pub struct SpreadCache {
    /// Ticker symbol → median spread in percent.
    pub spreads: HashMap<String, f64>,
}

/// Phase 16: Load spread_cache.toml with safe fallback.
pub fn load_spread_cache(config_dir: &Path) -> SpreadCache {
    let path = config_dir.join("spread_cache.toml");
    _load_spread(&path).unwrap_or_default()
}

fn _load_spread(path: &Path) -> Result<SpreadCache, String> {
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let raw: HashMap<String, toml::Value> = toml::from_str(&content).map_err(|e| e.to_string())?;

    let mut spreads = HashMap::new();
    if let Some(toml::Value::Table(tbl)) = raw.get("spreads") {
        for (k, v) in tbl {
            if let Some(f) = v.as_float() {
                spreads.insert(k.clone(), f);
            }
        }
    }
    Ok(SpreadCache { spreads })
}

/// Phase 16: GARCH parameters from Ouroboros nightly fit.
#[derive(Debug, Clone, Default)]
pub struct GarchParams {
    /// Ticker symbol → (omega, alpha, beta) GARCH(1,1) params.
    pub params: HashMap<String, (f64, f64, f64)>,
}

/// Phase 16: Load GARCH parameters. Tries .toml first, then .json fallback.
pub fn load_garch_params(config_dir: &Path) -> GarchParams {
    let toml_path = config_dir.join("garch_params.toml");
    if let Ok(params) = _load_garch_toml(&toml_path) {
        return params;
    }
    let json_path = config_dir.join("garch_params.json");
    if let Ok(params) = _load_garch_json(&json_path) {
        eprintln!("GARCH: loaded from garch_params.json (toml not found/invalid)");
        return params;
    }
    eprintln!("GARCH: no garch_params.toml or .json found — using defaults");
    GarchParams::default()
}

#[derive(Deserialize)]
struct RawGarchEntry {
    omega: f64,
    alpha: f64,
    beta: f64,
}

fn _load_garch_toml(path: &Path) -> Result<GarchParams, String> {
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let raw: HashMap<String, RawGarchEntry> =
        toml::from_str(&content).map_err(|e| e.to_string())?;

    let params = raw
        .into_iter()
        .map(|(k, v)| (k, (v.omega, v.alpha, v.beta)))
        .collect();
    Ok(GarchParams { params })
}

fn _load_garch_json(path: &Path) -> Result<GarchParams, String> {
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let raw: HashMap<String, RawGarchEntry> =
        serde_json::from_str(&content).map_err(|e| e.to_string())?;

    let params = raw
        .into_iter()
        .map(|(k, v)| (k, (v.omega, v.alpha, v.beta)))
        .collect();
    Ok(GarchParams { params })
}

/// Phase 16: FX rates from Ouroboros nightly.
#[derive(Debug, Clone, Default)]
pub struct FxRates {
    /// Currency pair → rate (e.g., "EURGBP" → 0.86).
    pub rates: HashMap<String, f64>,
}

/// Phase 16: Load fx_rates.toml with safe fallback.
pub fn load_fx_rates(config_dir: &Path) -> FxRates {
    let path = config_dir.join("fx_rates.toml");
    _load_fx(&path).unwrap_or_default()
}

fn _load_fx(path: &Path) -> Result<FxRates, String> {
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let raw: HashMap<String, toml::Value> = toml::from_str(&content).map_err(|e| e.to_string())?;

    let mut rates = HashMap::new();
    if let Some(toml::Value::Table(tbl)) = raw.get("rates") {
        for (k, v) in tbl {
            if let Some(f) = v.as_float() {
                rates.insert(k.clone(), f);
            }
        }
    }
    Ok(FxRates { rates })
}

// ═══════════════════════════════════════════════════════════════════════════════
// Rotation Plan — live_rotation_plan.json loader
// ═══════════════════════════════════════════════════════════════════════════════

/// Per-symbol score from dynamic universe rotation plan.
#[derive(Debug, Clone)]
pub struct RotationEntry {
    pub symbol: String,
    pub final_score: f64,
    pub base_score: f64,
    pub capped_boost: f64,
    pub exchange: String,
    pub sector: String,
}

/// Loaded rotation plan from dynamic_universe.py.
#[derive(Debug, Clone, Default)]
pub struct RotationPlan {
    /// Symbol → final_score for Kelly weighting.
    pub scores: HashMap<String, f64>,
    /// Full entries for debugging/logging.
    pub entries: Vec<RotationEntry>,
    /// Timestamp of plan generation.
    pub generated: String,
}

/// Load live_rotation_plan.json with safe fallback.
/// Returns empty plan if file missing or malformed (sizing unaffected).
pub fn load_rotation_plan(config_dir: &Path) -> RotationPlan {
    let path = config_dir.join("live_rotation_plan.json");
    _load_rotation(&path).unwrap_or_default()
}

fn _load_rotation(path: &Path) -> Result<RotationPlan, String> {
    let content = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let raw: serde_json::Value = serde_json::from_str(&content).map_err(|e| e.to_string())?;

    let generated = raw.get("generated")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let mut scores = HashMap::new();
    let mut entries = Vec::new();

    if let Some(live_100) = raw.get("live_100").and_then(|v| v.as_array()) {
        for item in live_100 {
            let symbol = item.get("symbol").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let final_score = item.get("final_score").and_then(|v| v.as_f64()).unwrap_or(0.5);
            let base_score = item.get("base_score").and_then(|v| v.as_f64()).unwrap_or(0.5);
            let capped_boost = item.get("capped_boost").and_then(|v| v.as_f64()).unwrap_or(0.0);
            let exchange = item.get("exchange").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let sector = item.get("sector").and_then(|v| v.as_str()).unwrap_or("").to_string();

            if !symbol.is_empty() {
                scores.insert(symbol.clone(), final_score);
                entries.push(RotationEntry {
                    symbol, final_score, base_score, capped_boost, exchange, sector,
                });
            }
        }
    }

    Ok(RotationPlan { scores, entries, generated })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_load_missing_returns_defaults() {
        let dw = load_dynamic_weights(Path::new("/nonexistent"));
        assert!((dw.bayesian_win_rate - 0.5).abs() < 0.001);
        assert_eq!(dw.trade_count, 0);
        assert!((dw.chandelier_atr_mult - 3.0).abs() < 0.001);

        let uc = load_universe_classification(Path::new("/nonexistent"));
        assert!(uc.tier1.is_empty());
        assert!(uc.locked.is_empty());
    }

    #[test]
    fn test_load_valid_dynamic_weights() {
        let dir = tempfile::tempdir().expect("tempdir");
        let content = r#"
schema_version = 1

[bayesian]
win_rate = 0.650000
trade_count = 42
sharpe_ratio = 1.230000
dsr = 0.850000
dsr_significant = true

[exit]
chandelier_atr_mult = 3.20
rung5_rate = 0.4500

[regime]
best = "bull_quiet"
worst = "bear_volatile"
bull_quiet = 1.00
bear_volatile = 0.50

[kelly_fractions]
t1 = 0.120000
t2 = 0.080000
"#;
        let path = dir.path().join("dynamic_weights.toml");
        std::fs::write(&path, content).expect("write");

        let dw = load_dynamic_weights(dir.path());
        assert!((dw.bayesian_win_rate - 0.65).abs() < 0.001);
        assert_eq!(dw.trade_count, 42);
        assert!(dw.dsr_significant);
        assert!((dw.chandelier_atr_mult - 3.2).abs() < 0.01);
        assert_eq!(dw.regime_best, "bull_quiet");
        assert!((dw.kelly_fractions["t1"] - 0.12).abs() < 0.001);
    }

    #[test]
    fn test_load_valid_universe_classification() {
        let dir = tempfile::tempdir().expect("tempdir");
        let content = r#"
schema_version = 1

[tiers]
tier1 = [1, 2, 3]
tier2 = [4, 5]
tier3 = [6]
locked = [7]
"#;
        let path = dir.path().join("universe_classification.toml");
        std::fs::write(&path, content).expect("write");

        let uc = load_universe_classification(dir.path());
        assert_eq!(uc.tier1, vec![1, 2, 3]);
        assert_eq!(uc.tier2, vec![4, 5]);
        assert_eq!(uc.tier3, vec![6]);
        assert_eq!(uc.locked, vec![7]);
    }

    #[test]
    fn test_malformed_toml_returns_defaults() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("dynamic_weights.toml");
        let mut f = std::fs::File::create(&path).expect("create");
        f.write_all(b"this is not valid toml {{{}").expect("write");
        drop(f);

        let dw = load_dynamic_weights(dir.path());
        assert!((dw.bayesian_win_rate - 0.5).abs() < 0.001);
    }

    #[test]
    fn test_spread_cache_loads() {
        let dir = tempfile::tempdir().expect("tempdir");
        let content = r#"
[spreads]
"QQQ3.L" = 0.15
"3LUS.L" = 0.22
"NVD3.L" = 0.08
"#;
        std::fs::write(dir.path().join("spread_cache.toml"), content).expect("write");
        let sc = load_spread_cache(dir.path());
        assert_eq!(sc.spreads.len(), 3);
        assert!((sc.spreads["QQQ3.L"] - 0.15).abs() < 0.001);
    }

    #[test]
    fn test_spread_cache_missing_returns_empty() {
        let sc = load_spread_cache(Path::new("/nonexistent"));
        assert!(sc.spreads.is_empty());
    }

    #[test]
    fn test_garch_params_loads() {
        let dir = tempfile::tempdir().expect("tempdir");
        let content = r#"
[QQQ3]
omega = 0.000001
alpha = 0.09
beta = 0.90
"#;
        std::fs::write(dir.path().join("garch_params.toml"), content).expect("write");
        let gp = load_garch_params(dir.path());
        let (omega, alpha, beta) = gp.params["QQQ3"];
        assert!((alpha - 0.09).abs() < 0.001);
        assert!((beta - 0.90).abs() < 0.001);
        assert!(omega > 0.0);
    }

    #[test]
    fn test_fx_rates_loads() {
        let dir = tempfile::tempdir().expect("tempdir");
        let content = r#"
[rates]
EURGBP = 0.86
CHFGBP = 0.89
SEKGBP = 0.074
"#;
        std::fs::write(dir.path().join("fx_rates.toml"), content).expect("write");
        let fx = load_fx_rates(dir.path());
        assert_eq!(fx.rates.len(), 3);
        assert!((fx.rates["EURGBP"] - 0.86).abs() < 0.001);
    }

    #[test]
    fn test_morning_boot_fallback_sequence() {
        // Simulate: dynamic_weights exists but universe_classification doesn't
        let dir = tempfile::tempdir().expect("tempdir");
        let dw_content = r#"
schema_version = 1
[bayesian]
win_rate = 0.700000
trade_count = 20
sharpe_ratio = 0.500000
dsr = 0.600000
dsr_significant = false
[exit]
chandelier_atr_mult = 2.80
rung5_rate = 0.3000
[regime]
best = "bull_quiet"
worst = "bear_volatile"
"#;
        std::fs::write(dir.path().join("dynamic_weights.toml"), dw_content).expect("write");

        let dw = load_dynamic_weights(dir.path());
        assert!((dw.bayesian_win_rate - 0.7).abs() < 0.001);

        // universe_classification doesn't exist → defaults
        let uc = load_universe_classification(dir.path());
        assert!(uc.tier1.is_empty());
    }
}
