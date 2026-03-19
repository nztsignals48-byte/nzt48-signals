//! P21: Market configuration — ticker lists for each trading mode.
//! Mode A (23:00-08:00 UTC): Asian markets (TSE, HKEX)
//! Mode B (08:00-14:30 UTC): European + LSE
//! Mode B+ (14:30-16:30 UTC): European + US overlap
//! Mode C (16:35-21:00 UTC): US-only session
//! Dark (21:00-23:00 UTC): No trading
//!
//! Static fallback lists — overridden by dynamic watchlist rotation when available.

/// Ticker configuration for different trading modes.
pub struct MarketConfig {
    /// 12 LSE leveraged ETPs (ISA core set — always included in B/B+/C).
    pub lse_12: Vec<&'static str>,
    /// TSE (Tokyo Stock Exchange) — top 20 most liquid.
    pub tse_sample: Vec<&'static str>,
    /// HKEX (Hong Kong) — top 20 most liquid.
    pub hkex_sample: Vec<&'static str>,
    /// ASX (Australian) — REMOVED (no IBKR data subscription).
    pub asx_sample: Vec<&'static str>,
    /// XETRA (Frankfurt) — 13 stocks.
    pub xetra_sample: Vec<&'static str>,
    /// Euronext (Paris/Amsterdam) — 8 stocks.
    pub euronext_sample: Vec<&'static str>,
    /// US equities (NASDAQ/NYSE) — 30 most liquid for ModeC.
    pub us_equities: Vec<&'static str>,
}

impl MarketConfig {
    pub fn new() -> Self {
        Self {
            lse_12: vec![
                // LSE leveraged ETPs (12 ISA instruments)
                // Canonical LSE ticker names (validated from isa_universe_master.json):
                //   NVD3.L (NOT 3NVD.L), TSL3.L (NOT 3TSL.L),
                //   TSM3.L (NOT 3TSM.L), MU2.L (NOT 2MU.L)
                "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
                "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "5SPY.L",
            ],
            tse_sample: vec![
                // Top TSE stocks (20 unique — matches contracts.toml)
                "7203", "6902", "8035", "6758", "6861", "8306", "6954",
                "9432", "8591", "9984", "8766", "3382", "6869", "4502",
                "9201", "8802", "5401", "1925", "1928", "6501",
            ],
            hkex_sample: vec![
                // Top HKEX stocks (sample)
                "0001", "0175", "0691", "0700", "0883", "1211", "1299",
                "1398", "1088", "6862", "9618", "6823", "0288", "0857",
                "1177", "0142", "0689", "0939", "0006", "0388",
            ],
            asx_sample: vec![
                // ASX REMOVED — no IBKR data subscription active
                // Re-add when ASX Total (NP,L2) subscription is active (AUD 25/mo)
            ],
            xetra_sample: vec![
                // XETRA stocks (14 — matches contracts.toml, BEI added)
                "SAP", "SIE", "IFX", "VOW3", "BMW", "MBG", "ADS",
                "MUV2", "HEI", "RWE", "EOAN", "DTE", "BEI",
            ],
            euronext_sample: vec![
                // Euronext stocks (Paris/Amsterdam/Helsinki)
                "OR", "NOKIA", "TTE", "SAN", "MC", "ASML",
            ],
            us_equities: vec![
                // US equities — 30 most liquid NASDAQ/NYSE via SMART routing
                "AAPL", "MSFT", "NVDA", "TSLA", "GOOG", "META", "AMZN", "AMD",
                "AVGO", "CRM", "NFLX", "ORCL", "MU", "QCOM", "AMAT", "KLAC",
                "LRCX", "MRVL", "ARM", "PLTR", "SMCI", "JPM", "V", "UNH",
                "XOM", "LLY", "COIN", "MSTR", "SNOW", "INTC",
            ],
        }
    }

    /// Mode A (Asian session): TSE + HKEX (ASX removed)
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

    /// Mode B+ (US overlap): LSE + XETRA + Euronext + US equities
    pub fn mode_bplus_tickers(&self) -> Vec<&'static str> {
        let mut result = Vec::new();
        result.extend_from_slice(&self.lse_12);
        result.extend_from_slice(&self.xetra_sample);
        result.extend_from_slice(&self.euronext_sample);
        result.extend_from_slice(&self.us_equities);
        result
    }

    /// Mode C (US session): ISA core ETPs + US equities
    pub fn mode_c_tickers(&self) -> Vec<&'static str> {
        let mut result = Vec::new();
        result.extend_from_slice(&self.lse_12);  // Always include ISA ETPs
        result.extend_from_slice(&self.us_equities);
        result
    }

    /// Unified: all markets combined (static fallback for when watchlist is empty).
    /// ISA core first, then global tickers. Capped at 100 (IBKR paper max).
    pub fn all_markets_tickers(&self) -> Vec<&'static str> {
        let mut result = Vec::new();
        result.extend_from_slice(&self.lse_12);
        result.extend_from_slice(&self.us_equities);
        result.extend_from_slice(&self.tse_sample);
        result.extend_from_slice(&self.hkex_sample);
        result.extend_from_slice(&self.xetra_sample);
        result.extend_from_slice(&self.euronext_sample);
        result.truncate(100); // IBKR paper max
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
        // TSE (20) + HKEX (20) + ASX (0, removed) = 40
        assert_eq!(tickers.len(), 40);
        assert!(tickers.contains(&"7203")); // TSE sample
        assert!(tickers.contains(&"0001")); // HKEX sample
    }

    #[test]
    fn test_market_config_mode_b() {
        let cfg = MarketConfig::new();
        let tickers = cfg.mode_b_tickers();
        // LSE (12) + XETRA (13) + Euronext (6) = 31
        assert_eq!(tickers.len(), 31);
        assert!(tickers.contains(&"QQQ3.L")); // LSE sample
        assert!(tickers.contains(&"SAP"));    // XETRA sample
        assert!(tickers.contains(&"OR"));     // Euronext sample
    }

    #[test]
    fn test_market_config_mode_bplus() {
        let cfg = MarketConfig::new();
        let tickers = cfg.mode_bplus_tickers();
        // LSE (12) + XETRA (13) + Euronext (6) + US (30) = 61
        assert_eq!(tickers.len(), 61);
        assert!(tickers.contains(&"QQQ3.L")); // ISA core
        assert!(tickers.contains(&"NVDA"));   // US equity
        assert!(tickers.contains(&"SAP"));    // XETRA
    }

    #[test]
    fn test_market_config_mode_c() {
        let cfg = MarketConfig::new();
        let tickers = cfg.mode_c_tickers();
        // LSE (12) + US (30) = 42
        assert_eq!(tickers.len(), 42);
        assert!(tickers.contains(&"QQQ3.L")); // ISA core always included
        assert!(tickers.contains(&"AAPL"));   // US equity
        assert!(tickers.contains(&"TSLA"));   // US equity
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
        assert!(cfg.lse_12.contains(&"QQQ3.L"));
        assert!(cfg.lse_12.contains(&"3LUS.L"));
        assert!(cfg.lse_12.contains(&"5SPY.L"));
    }

    #[test]
    fn test_us_equities_count() {
        let cfg = MarketConfig::new();
        assert_eq!(cfg.us_equities.len(), 30);
        assert!(cfg.us_equities.contains(&"AAPL"));
        assert!(cfg.us_equities.contains(&"INTC"));
    }
}
