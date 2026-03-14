//! P21: Market configuration — ticker lists for each trading mode.
//! Mode A (23:00-08:00 UTC): Asian markets (TSE, HKEX, ASX)
//! Mode B (08:00-16:30 UTC): European + LSE
//! Dark (16:30-23:00 UTC): No trading

/// Ticker configuration for different trading modes.
pub struct MarketConfig {
    /// 12 LSE leveraged ETPs (Mode B core set).
    pub lse_12: Vec<&'static str>,
    /// TSE (Tokyo Stock Exchange) — ~3,900 most liquid.
    pub tse_sample: Vec<&'static str>,
    /// HKEX (Hong Kong) — ~2,500 most liquid.
    pub hkex_sample: Vec<&'static str>,
    /// ASX (Australian) — ~2,200 most liquid.
    pub asx_sample: Vec<&'static str>,
    /// XETRA (Frankfurt) — sample of ~10k (phase 2).
    pub xetra_sample: Vec<&'static str>,
    /// Euronext (Paris/Amsterdam) — sample of ~1.5k (phase 2).
    pub euronext_sample: Vec<&'static str>,
}

impl MarketConfig {
    pub fn new() -> Self {
        Self {
            lse_12: vec![
                // LSE leveraged ETPs (12 ISA instruments)
                "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
                "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
            ],
            tse_sample: vec![
                // Top TSE stocks (sample for MVP)
                "7203", "6902", "8035", "6758", "6861", "8306", "6954",
                "9432", "8591", "9984", "8766", "3382", "6869", "6903",
                "6758", "6861", "8035", "6902", "7203", "9432",
            ],
            hkex_sample: vec![
                // Top HKEX stocks (sample)
                "0001", "0175", "0691", "0700", "0883", "1211", "1299",
                "1398", "1088", "6862", "9618", "6823", "0288", "0857",
                "1177", "0142", "0689", "0939", "0006", "0388",
            ],
            asx_sample: vec![
                // Top ASX stocks (sample)
                "BHP", "CBA", "CSL", "FMG", "NAB", "WBC", "MQG", "RIO",
                "ANZ", "GPT", "WES", "TCL", "APA", "IAG", "QAN", "SYD",
                "VAS", "ORA", "AGL", "AFL",
            ],
            xetra_sample: vec![
                // Sample XETRA stocks (phase 2, not MVP)
                "SAP", "SIE", "IFX", "DAI", "VOW3", "BMW", "MBG", "ADS",
                "MUV2", "HEI", "RWE", "E.ON", "DTE", "EOAN",
            ],
            euronext_sample: vec![
                // Sample Euronext stocks (phase 2, not MVP)
                "OR", "NOKIA", "TOTAL", "SANOFI", "LVMH", "ASML",
            ],
        }
    }

    /// Mode A (Asian session): TSE + HKEX + ASX
    pub fn mode_a_tickers(&self) -> Vec<&'static str> {
        let mut result = Vec::new();
        result.extend_from_slice(&self.tse_sample);
        result.extend_from_slice(&self.hkex_sample);
        result.extend_from_slice(&self.asx_sample);
        result
    }

    /// Mode B (European session): LSE + XETRA + Euronext
    pub fn mode_b_tickers(&self) -> Vec<&'static str> {
        let mut result = Vec::new();
        result.extend_from_slice(&self.lse_12);
        result.extend_from_slice(&self.xetra_sample);
        result.extend_from_slice(&self.euronext_sample);
        result
    }

    /// Dark hours: no trading
    pub fn dark_tickers(&self) -> Vec<&'static str> {
        vec![]
    }
}

impl Default for MarketConfig {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_market_config_mode_a() {
        let cfg = MarketConfig::new();
        let tickers = cfg.mode_a_tickers();
        // TSE (20) + HKEX (20) + ASX (20) = 60
        assert_eq!(tickers.len(), 60);
        assert!(tickers.contains(&"7203")); // TSE sample
        assert!(tickers.contains(&"0001")); // HKEX sample
        assert!(tickers.contains(&"BHP"));  // ASX sample
    }

    #[test]
    fn test_market_config_mode_b() {
        let cfg = MarketConfig::new();
        let tickers = cfg.mode_b_tickers();
        // LSE (12) + XETRA (14) + Euronext (6) = 32
        assert_eq!(tickers.len(), 32);
        assert!(tickers.contains(&"QQQ3.L")); // LSE sample
        assert!(tickers.contains(&"SAP"));    // XETRA sample
        assert!(tickers.contains(&"OR"));     // Euronext sample
    }

    #[test]
    fn test_market_config_dark() {
        let cfg = MarketConfig::new();
        assert_eq!(cfg.dark_tickers().len(), 0);
    }

    #[test]
    fn test_lse_12_fixed() {
        let cfg = MarketConfig::new();
        assert_eq!(cfg.lse_12.len(), 12);
        // Verify core ISA instruments
        assert!(cfg.lse_12.contains(&"QQQ3.L"));
        assert!(cfg.lse_12.contains(&"3LUS.L"));
        assert!(cfg.lse_12.contains(&"SP5L.L"));
    }
}
