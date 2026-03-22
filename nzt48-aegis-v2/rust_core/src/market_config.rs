//! P21: Market configuration — ticker lists for each trading mode.
//! Mode A (23:00-08:00 UTC): Asian markets (TSE, HKEX, SGX)
//! Mode B (08:00-14:30 UTC): European + LSE
//! Mode B+ (14:30-16:30 UTC): European + US overlap
//! Mode C (16:35-21:00 UTC): US-only session
//! Dark (21:00-23:00 UTC): No trading
//!
//! Loaded dynamically from contracts.toml — grouped by exchange.
//! Static fallback only used if contracts.toml cannot be read.

use std::path::Path;

/// Ticker configuration for different trading modes.
/// Loaded from contracts.toml, grouped by exchange.
pub struct MarketConfig {
    /// LSE leveraged ETPs (LSEETF exchange).
    pub lse: Vec<String>,
    /// TSE (Tokyo Stock Exchange).
    pub tse: Vec<String>,
    /// HKEX (Hong Kong).
    pub hkex: Vec<String>,
    /// SGX (Singapore).
    pub sgx: Vec<String>,
    /// XETRA (Frankfurt).
    pub xetra: Vec<String>,
    /// Euronext (Paris/Amsterdam).
    pub euronext: Vec<String>,
    /// US equities (NASDAQ/NYSE via SMART).
    pub us_equities: Vec<String>,
}

impl MarketConfig {
    /// Load from contracts.toml, grouping symbols by exchange.
    pub fn from_contracts(config_dir: &Path) -> Self {
        let contracts_path = config_dir.join("contracts.toml");
        if let Ok(content) = std::fs::read_to_string(&contracts_path) {
            if let Ok(table) = content.parse::<toml::Table>() {
                return Self::parse_contracts(&table);
            }
        }
        eprintln!("MARKET_CONFIG: contracts.toml not found or invalid, using empty config");
        Self::empty()
    }

    fn parse_contracts(table: &toml::Table) -> Self {
        let mut lse = Vec::new();
        let mut tse = Vec::new();
        let mut hkex = Vec::new();
        let mut sgx = Vec::new();
        let mut xetra = Vec::new();
        let mut euronext = Vec::new();
        let mut us_equities = Vec::new();

        if let Some(contracts) = table.get("contracts").and_then(|c| c.as_array()) {
            for contract in contracts {
                let symbol = contract.get("symbol").and_then(|s| s.as_str()).unwrap_or("");
                let exchange = contract.get("exchange").and_then(|e| e.as_str()).unwrap_or("");
                if symbol.is_empty() {
                    continue;
                }
                match exchange {
                    "LSEETF" => lse.push(symbol.to_string()),
                    "TSE" => tse.push(symbol.to_string()),
                    "HKEX" => hkex.push(symbol.to_string()),
                    "SGX" => sgx.push(symbol.to_string()),
                    "XETRA" | "IBIS" => xetra.push(symbol.to_string()),
                    "EURONEXT" | "AEB" | "XMAD" | "HEX" => euronext.push(symbol.to_string()),
                    "SMART" => us_equities.push(symbol.to_string()),
                    _ => {} // Unknown exchange — skip
                }
            }
        }

        eprintln!(
            "MARKET_CONFIG: Loaded from contracts.toml — LSE:{} TSE:{} HKEX:{} SGX:{} XETRA:{} EURONEXT:{} US:{}",
            lse.len(), tse.len(), hkex.len(), sgx.len(), xetra.len(), euronext.len(), us_equities.len()
        );

        Self { lse, tse, hkex, sgx, xetra, euronext, us_equities }
    }

    fn empty() -> Self {
        Self {
            lse: Vec::new(),
            tse: Vec::new(),
            hkex: Vec::new(),
            sgx: Vec::new(),
            xetra: Vec::new(),
            euronext: Vec::new(),
            us_equities: Vec::new(),
        }
    }

    /// Mode A (Asian session): TSE + HKEX + SGX
    pub fn mode_a_tickers(&self) -> Vec<&str> {
        let mut result: Vec<&str> = Vec::new();
        result.extend(self.tse.iter().map(|s| s.as_str()));
        result.extend(self.hkex.iter().map(|s| s.as_str()));
        result.extend(self.sgx.iter().map(|s| s.as_str()));
        result
    }

    /// Mode B (European session): LSE + XETRA + Euronext
    pub fn mode_b_tickers(&self) -> Vec<&str> {
        let mut result: Vec<&str> = Vec::new();
        result.extend(self.lse.iter().map(|s| s.as_str()));
        result.extend(self.xetra.iter().map(|s| s.as_str()));
        result.extend(self.euronext.iter().map(|s| s.as_str()));
        result
    }

    /// Mode B+ (US overlap): LSE + XETRA + Euronext + US
    pub fn mode_bplus_tickers(&self) -> Vec<&str> {
        let mut result = self.mode_b_tickers();
        result.extend(self.us_equities.iter().map(|s| s.as_str()));
        result
    }

    /// Mode C (US session): LSE + US
    pub fn mode_c_tickers(&self) -> Vec<&str> {
        let mut result: Vec<&str> = Vec::new();
        result.extend(self.lse.iter().map(|s| s.as_str()));
        result.extend(self.us_equities.iter().map(|s| s.as_str()));
        result
    }

    /// Unified: all markets combined (static fallback for when watchlist is empty).
    /// Capped at 100 (IBKR subscription limit).
    pub fn all_markets_tickers(&self) -> Vec<&str> {
        let mut result: Vec<&str> = Vec::new();
        result.extend(self.lse.iter().map(|s| s.as_str()));
        result.extend(self.us_equities.iter().map(|s| s.as_str()));
        result.extend(self.tse.iter().map(|s| s.as_str()));
        result.extend(self.hkex.iter().map(|s| s.as_str()));
        result.extend(self.sgx.iter().map(|s| s.as_str()));
        result.extend(self.xetra.iter().map(|s| s.as_str()));
        result.extend(self.euronext.iter().map(|s| s.as_str()));
        result.truncate(100);
        result
    }

    /// Dark hours: no trading
    pub fn dark_tickers(&self) -> Vec<&str> {
        vec![]
    }

    /// Total contract count across all exchanges.
    pub fn total_contracts(&self) -> usize {
        self.lse.len() + self.tse.len() + self.hkex.len() + self.sgx.len()
            + self.xetra.len() + self.euronext.len() + self.us_equities.len()
    }
}

impl Default for MarketConfig {
    fn default() -> Self {
        Self::from_contracts(Path::new("/app/config"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_toml() -> toml::Table {
        let content = r#"
[[contracts]]
symbol = "QQQ3.L"
exchange = "LSEETF"
[[contracts]]
symbol = "AAPL"
exchange = "SMART"
[[contracts]]
symbol = "7203"
exchange = "TSE"
[[contracts]]
symbol = "0700"
exchange = "HKEX"
[[contracts]]
symbol = "SAP"
exchange = "XETRA"
[[contracts]]
symbol = "OR"
exchange = "EURONEXT"
[[contracts]]
symbol = "D05"
exchange = "SGX"
"#;
        content.parse::<toml::Table>().unwrap()
    }

    #[test]
    fn test_parse_contracts() {
        let table = sample_toml();
        let cfg = MarketConfig::parse_contracts(&table);
        assert_eq!(cfg.lse.len(), 1);
        assert_eq!(cfg.us_equities.len(), 1);
        assert_eq!(cfg.tse.len(), 1);
        assert_eq!(cfg.hkex.len(), 1);
        assert_eq!(cfg.xetra.len(), 1);
        assert_eq!(cfg.euronext.len(), 1);
        assert_eq!(cfg.sgx.len(), 1);
        assert_eq!(cfg.total_contracts(), 7);
    }

    #[test]
    fn test_mode_a() {
        let table = sample_toml();
        let cfg = MarketConfig::parse_contracts(&table);
        let tickers = cfg.mode_a_tickers();
        assert!(tickers.contains(&"7203"));
        assert!(tickers.contains(&"0700"));
        assert!(tickers.contains(&"D05"));
    }

    #[test]
    fn test_mode_b() {
        let table = sample_toml();
        let cfg = MarketConfig::parse_contracts(&table);
        let tickers = cfg.mode_b_tickers();
        assert!(tickers.contains(&"QQQ3.L"));
        assert!(tickers.contains(&"SAP"));
        assert!(tickers.contains(&"OR"));
    }

    #[test]
    fn test_all_markets_cap_100() {
        let table = sample_toml();
        let cfg = MarketConfig::parse_contracts(&table);
        let tickers = cfg.all_markets_tickers();
        assert!(tickers.len() <= 100);
    }

    #[test]
    fn test_empty_on_missing() {
        let cfg = MarketConfig::from_contracts(Path::new("/nonexistent/path"));
        assert_eq!(cfg.total_contracts(), 0);
    }
}
