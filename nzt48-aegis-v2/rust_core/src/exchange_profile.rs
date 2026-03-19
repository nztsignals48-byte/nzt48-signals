//! Phase 12: European exchange profiles.
//! 15 ISA-eligible exchanges with trading hours, tick sizes, and execution rules.

use crate::currency::Currency;

/// A European exchange profile.
#[derive(Clone, Debug)]
pub struct ExchangeProfile {
    /// MIC code (ISO 10383).
    pub mic: &'static str,
    /// Human-readable name.
    pub name: &'static str,
    /// Trading currency.
    pub currency: Currency,
    /// Continuous trading start (seconds from midnight UTC).
    pub open_utc_secs: u32,
    /// Continuous trading end (seconds from midnight UTC).
    pub close_utc_secs: u32,
    /// Closing auction start (seconds from midnight UTC). 0 = no closing auction.
    pub closing_auction_utc_secs: u32,
    /// Tick size for prices >= 1.0 in local currency.
    pub tick_size_over_1: f64,
    /// Tick size for prices < 1.0 in local currency.
    pub tick_size_under_1: f64,
    /// Whether IBKR supports this exchange for ISA accounts.
    pub isa_eligible: bool,
    /// Country code (ISO 3166-1 alpha-2).
    pub country: &'static str,
    /// Whether this exchange has a Financial Transaction Tax.
    pub has_ftt: bool,
    /// FTT rate (e.g., 0.003 = 0.3% for France). 0.0 if no FTT.
    pub ftt_rate: f64,
    /// FTT market cap threshold in EUR (e.g., 1B for France). 0 = applies to all.
    pub ftt_market_cap_threshold_eur: f64,
    /// Whether FTT has intraday exemption (Amendment A2).
    pub ftt_intraday_exempt: bool,
}

impl ExchangeProfile {
    /// Is the exchange open at this UTC time (seconds from midnight)?
    /// Note: open_utc_secs/close_utc_secs are GMT baselines. For BST-affected exchanges
    /// (XLON, XDUB), the engine's ModeB gate uses London local time via the Clock,
    /// so this static check is only used for non-critical auxiliary functions.
    pub fn is_open(&self, utc_secs: u32) -> bool {
        utc_secs >= self.open_utc_secs && utc_secs < self.close_utc_secs
    }

    /// Is the closing auction active? (Amendment A3: XETRA closing auction)
    pub fn is_closing_auction(&self, utc_secs: u32) -> bool {
        if self.closing_auction_utc_secs == 0 {
            return false;
        }
        // Closing auction: from auction start to auction + 5 min
        utc_secs >= self.closing_auction_utc_secs && utc_secs < self.closing_auction_utc_secs + 300
    }

    /// Round price to valid tick size for this exchange.
    pub fn round_tick(&self, price: f64) -> f64 {
        let tick = if price < 1.0 {
            self.tick_size_under_1
        } else {
            self.tick_size_over_1
        };
        (price / tick).floor() * tick
    }

    /// FTT cost for a given trade value in local currency.
    /// Returns 0.0 if no FTT or below market cap threshold.
    pub fn ftt_cost(&self, trade_value_local: f64, market_cap_eur: f64, is_intraday: bool) -> f64 {
        if !self.has_ftt || self.ftt_rate <= 0.0 {
            return 0.0;
        }
        // Amendment A2: intraday exemption
        if self.ftt_intraday_exempt && is_intraday {
            return 0.0;
        }
        // Market cap threshold check
        if self.ftt_market_cap_threshold_eur > 0.0
            && market_cap_eur < self.ftt_market_cap_threshold_eur
        {
            return 0.0;
        }
        trade_value_local * self.ftt_rate
    }
}

/// Registry of all supported European exchanges.
pub struct ExchangeRegistry {
    profiles: Vec<ExchangeProfile>,
}

impl ExchangeRegistry {
    pub fn new() -> Self {
        Self {
            profiles: build_exchange_profiles(),
        }
    }

    /// Look up an exchange by MIC code.
    pub fn by_mic(&self, mic: &str) -> Option<&ExchangeProfile> {
        self.profiles.iter().find(|p| p.mic == mic)
    }

    /// All ISA-eligible exchanges.
    pub fn isa_eligible(&self) -> Vec<&ExchangeProfile> {
        self.profiles.iter().filter(|p| p.isa_eligible).collect()
    }

    /// All exchange profiles.
    pub fn all(&self) -> &[ExchangeProfile] {
        &self.profiles
    }

    /// Number of registered exchanges.
    pub fn len(&self) -> usize {
        self.profiles.len()
    }

    pub fn is_empty(&self) -> bool {
        self.profiles.is_empty()
    }
}

impl Default for ExchangeRegistry {
    fn default() -> Self {
        Self::new()
    }
}

/// Build the 15 European exchange profiles.
fn build_exchange_profiles() -> Vec<ExchangeProfile> {
    vec![
        // UK
        ExchangeProfile {
            mic: "XLON",
            name: "London Stock Exchange",
            currency: Currency::GBP,
            open_utc_secs: 8 * 3600,
            close_utc_secs: 16 * 3600 + 30 * 60,
            closing_auction_utc_secs: 16 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "GB",
            has_ftt: true,
            ftt_rate: 0.005, // 0.5% stamp duty
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false, // UK stamp duty: no intraday exemption
        },
        // Germany
        ExchangeProfile {
            mic: "XETR",
            name: "XETRA (Deutsche Börse)",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60, // Amendment A3
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "DE",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // France
        ExchangeProfile {
            mic: "XPAR",
            name: "Euronext Paris",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "FR",
            has_ftt: true,
            ftt_rate: 0.003, // 0.3% French FTT
            ftt_market_cap_threshold_eur: 1_000_000_000.0, // €1B market cap
            ftt_intraday_exempt: true, // Amendment A2
        },
        // Netherlands
        ExchangeProfile {
            mic: "XAMS",
            name: "Euronext Amsterdam",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "NL",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Belgium
        ExchangeProfile {
            mic: "XBRU",
            name: "Euronext Brussels",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "BE",
            has_ftt: true,
            ftt_rate: 0.0012, // 0.12% Belgian TOB
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Portugal
        ExchangeProfile {
            mic: "XLIS",
            name: "Euronext Lisbon",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "PT",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Italy
        ExchangeProfile {
            mic: "XMIL",
            name: "Borsa Italiana (Milan)",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "IT",
            has_ftt: true,
            ftt_rate: 0.001, // 0.1% Italian FTT
            ftt_market_cap_threshold_eur: 500_000_000.0, // €500M
            ftt_intraday_exempt: true, // Amendment A2
        },
        // Spain
        ExchangeProfile {
            mic: "XMAD",
            name: "Bolsa de Madrid",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "ES",
            has_ftt: true,
            ftt_rate: 0.002, // 0.2% Spanish FTT
            ftt_market_cap_threshold_eur: 1_000_000_000.0, // €1B
            ftt_intraday_exempt: false,
        },
        // Switzerland
        ExchangeProfile {
            mic: "XSWX",
            name: "SIX Swiss Exchange",
            currency: Currency::CHF,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "CH",
            has_ftt: true,
            ftt_rate: 0.00075, // 0.075% Swiss stamp tax (per side)
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Sweden
        ExchangeProfile {
            mic: "XSTO",
            name: "Nasdaq Stockholm",
            currency: Currency::SEK,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "SE",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Norway
        ExchangeProfile {
            mic: "XOSL",
            name: "Oslo Børs",
            currency: Currency::NOK,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 14 * 3600 + 20 * 60,
            closing_auction_utc_secs: 14 * 3600 + 20 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "NO",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Denmark
        ExchangeProfile {
            mic: "XCSE",
            name: "Nasdaq Copenhagen",
            currency: Currency::DKK,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600,
            closing_auction_utc_secs: 15 * 3600,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "DK",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Finland
        ExchangeProfile {
            mic: "XHEL",
            name: "Nasdaq Helsinki",
            currency: Currency::EUR,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 30 * 60,
            closing_auction_utc_secs: 15 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "FI",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Ireland
        ExchangeProfile {
            mic: "XDUB",
            name: "Euronext Dublin",
            currency: Currency::EUR,
            open_utc_secs: 8 * 3600,
            close_utc_secs: 16 * 3600 + 30 * 60,
            closing_auction_utc_secs: 16 * 3600 + 30 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "IE",
            has_ftt: true,
            ftt_rate: 0.01, // 1% Irish stamp duty
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
        // Poland
        ExchangeProfile {
            mic: "XWAR",
            name: "Warsaw Stock Exchange",
            currency: Currency::PLN,
            open_utc_secs: 7 * 3600,
            close_utc_secs: 15 * 3600 + 50 * 60,
            closing_auction_utc_secs: 15 * 3600 + 50 * 60,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: true,
            country: "PL",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_registry_has_15_exchanges() {
        let reg = ExchangeRegistry::new();
        assert_eq!(reg.len(), 15);
    }

    #[test]
    fn test_all_isa_eligible() {
        let reg = ExchangeRegistry::new();
        assert_eq!(reg.isa_eligible().len(), 15);
    }

    #[test]
    fn test_lookup_by_mic() {
        let reg = ExchangeRegistry::new();
        let xlon = reg.by_mic("XLON").expect("XLON exists");
        assert_eq!(xlon.currency, Currency::GBP);
        assert_eq!(xlon.country, "GB");

        let xetr = reg.by_mic("XETR").expect("XETR exists");
        assert_eq!(xetr.currency, Currency::EUR);
        assert_eq!(xetr.country, "DE");
    }

    #[test]
    fn test_exchange_open_hours() {
        let reg = ExchangeRegistry::new();
        let xlon = reg.by_mic("XLON").expect("XLON");
        assert!(!xlon.is_open(7 * 3600)); // 07:00 UTC
        assert!(xlon.is_open(8 * 3600)); // 08:00 UTC
        assert!(xlon.is_open(12 * 3600)); // 12:00 UTC
        assert!(!xlon.is_open(16 * 3600 + 30 * 60)); // 16:30 UTC (at close)
    }

    #[test]
    fn test_french_ftt_with_threshold() {
        let reg = ExchangeRegistry::new();
        let xpar = reg.by_mic("XPAR").expect("XPAR");

        // Below €1B market cap: no FTT
        let cost = xpar.ftt_cost(10000.0, 500_000_000.0, false);
        assert!((cost).abs() < 0.001);

        // Above €1B market cap: 0.3% FTT
        let cost = xpar.ftt_cost(10000.0, 2_000_000_000.0, false);
        assert!((cost - 30.0).abs() < 0.01); // 10000 * 0.003

        // Intraday exempt (Amendment A2)
        let cost = xpar.ftt_cost(10000.0, 2_000_000_000.0, true);
        assert!((cost).abs() < 0.001);
    }

    #[test]
    fn test_uk_stamp_duty_no_intraday_exempt() {
        let reg = ExchangeRegistry::new();
        let xlon = reg.by_mic("XLON").expect("XLON");

        // UK stamp duty: 0.5%, no market cap threshold, no intraday exemption
        let cost = xlon.ftt_cost(10000.0, 0.0, false);
        assert!((cost - 50.0).abs() < 0.01);

        // Still charged intraday (no exemption for UK)
        let cost = xlon.ftt_cost(10000.0, 0.0, true);
        assert!((cost - 50.0).abs() < 0.01);
    }

    #[test]
    fn test_tick_rounding() {
        let reg = ExchangeRegistry::new();
        let xetr = reg.by_mic("XETR").expect("XETR");
        assert!((xetr.round_tick(10.567) - 10.56).abs() < 0.001);
        assert!((xetr.round_tick(0.5678) - 0.567).abs() < 0.0001);
    }

    #[test]
    fn test_closing_auction() {
        let reg = ExchangeRegistry::new();
        let xetr = reg.by_mic("XETR").expect("XETR");
        // XETRA closes at 15:30 UTC, closing auction 15:30-15:35
        assert!(!xetr.is_closing_auction(15 * 3600 + 29 * 60));
        assert!(xetr.is_closing_auction(15 * 3600 + 30 * 60));
        assert!(xetr.is_closing_auction(15 * 3600 + 34 * 60));
        assert!(!xetr.is_closing_auction(15 * 3600 + 35 * 60));
    }
}
