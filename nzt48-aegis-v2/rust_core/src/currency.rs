//! Phase 12: Currency handling for European equity routing.
//! FX rate table, currency conversion, minimum fee awareness.

use std::collections::HashMap;

/// Supported trading currencies.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Currency {
    GBP,
    EUR,
    CHF,
    SEK,
    NOK,
    DKK,
    PLN,
    USD,
}

impl Currency {
    /// Parse from string (case-insensitive).
    pub fn from_str_code(s: &str) -> Option<Self> {
        match s.to_uppercase().as_str() {
            "GBP" => Some(Currency::GBP),
            "EUR" => Some(Currency::EUR),
            "CHF" => Some(Currency::CHF),
            "SEK" => Some(Currency::SEK),
            "NOK" => Some(Currency::NOK),
            "DKK" => Some(Currency::DKK),
            "PLN" => Some(Currency::PLN),
            "USD" => Some(Currency::USD),
            _ => None,
        }
    }

    /// ISO 4217 code.
    pub fn code(&self) -> &'static str {
        match self {
            Currency::GBP => "GBP",
            Currency::EUR => "EUR",
            Currency::CHF => "CHF",
            Currency::SEK => "SEK",
            Currency::NOK => "NOK",
            Currency::DKK => "DKK",
            Currency::PLN => "PLN",
            Currency::USD => "USD",
        }
    }
}

/// Route for currency conversion.
#[derive(Clone, Debug)]
pub struct CurrencyRoute {
    pub from: Currency,
    pub to: Currency,
    pub rate: f64,
}

/// FX rate table: rates relative to GBP (home currency).
/// Updated nightly by Ouroboros or on-demand from IBKR.
pub struct FxRateTable {
    /// Rates: currency → GBP rate (how many GBP per 1 unit of currency).
    rates_to_gbp: HashMap<Currency, f64>,
    /// IBKR FX minimum fee in GBP (Amendment A1: £2.00).
    pub fx_minimum_fee_gbp: f64,
    /// Last update timestamp (nanoseconds).
    pub last_update_ns: u64,
}

impl FxRateTable {
    pub fn new() -> Self {
        let mut rates = HashMap::new();
        // Default rates (updated nightly by Ouroboros)
        rates.insert(Currency::GBP, 1.0);
        rates.insert(Currency::EUR, 0.86); // 1 EUR ≈ £0.86
        rates.insert(Currency::CHF, 0.89); // 1 CHF ≈ £0.89
        rates.insert(Currency::SEK, 0.074); // 1 SEK ≈ £0.074
        rates.insert(Currency::NOK, 0.072); // 1 NOK ≈ £0.072
        rates.insert(Currency::DKK, 0.115); // 1 DKK ≈ £0.115
        rates.insert(Currency::PLN, 0.20); // 1 PLN ≈ £0.20
        rates.insert(Currency::USD, 0.79); // 1 USD ≈ £0.79
        Self {
            rates_to_gbp: rates,
            fx_minimum_fee_gbp: 2.0, // Amendment A1
            last_update_ns: 0,
        }
    }

    /// Convert an amount from one currency to GBP.
    pub fn to_gbp(&self, amount: f64, from: Currency) -> f64 {
        if from == Currency::GBP {
            return amount;
        }
        let rate = self.rates_to_gbp.get(&from).copied().unwrap_or(1.0);
        amount * rate
    }

    /// Convert GBP to another currency.
    pub fn from_gbp(&self, gbp_amount: f64, to: Currency) -> f64 {
        if to == Currency::GBP {
            return gbp_amount;
        }
        let rate = self.rates_to_gbp.get(&to).copied().unwrap_or(1.0);
        if rate > 0.0 {
            gbp_amount / rate
        } else {
            gbp_amount
        }
    }

    /// Update a rate.
    pub fn set_rate(&mut self, currency: Currency, rate_to_gbp: f64, now_ns: u64) {
        self.rates_to_gbp.insert(currency, rate_to_gbp);
        self.last_update_ns = now_ns;
    }

    /// Get GBP rate for a currency.
    pub fn rate(&self, currency: Currency) -> f64 {
        self.rates_to_gbp.get(&currency).copied().unwrap_or(1.0)
    }

    /// FX conversion cost for a position size in GBP.
    /// Amendment A1: £2.00 minimum fee. For positions < £1000, FX cost is disproportionate.
    /// Returns the FX cost in GBP (bid-ask spread + minimum fee consideration).
    pub fn fx_cost_gbp(&self, position_gbp: f64, currency: Currency) -> f64 {
        if currency == Currency::GBP {
            return 0.0;
        }
        // IBKR FX spread: ~0.002% (2 basis points) for major pairs
        let spread_cost = position_gbp * 0.00002;
        // Minimum fee dominates for small positions
        spread_cost.max(self.fx_minimum_fee_gbp)
    }

    /// Is the FX rate stale? (>24h since last update)
    pub fn is_stale(&self, now_ns: u64) -> bool {
        if self.last_update_ns == 0 {
            return true;
        }
        now_ns > self.last_update_ns + 86_400_000_000_000 // 24h in ns
    }
}

impl Default for FxRateTable {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_gbp_identity() {
        let fx = FxRateTable::new();
        assert!((fx.to_gbp(100.0, Currency::GBP) - 100.0).abs() < 0.001);
        assert!((fx.from_gbp(100.0, Currency::GBP) - 100.0).abs() < 0.001);
    }

    #[test]
    fn test_eur_to_gbp() {
        let fx = FxRateTable::new();
        let gbp = fx.to_gbp(100.0, Currency::EUR);
        assert!((gbp - 86.0).abs() < 0.01); // 100 EUR * 0.86
    }

    #[test]
    fn test_gbp_to_eur_roundtrip() {
        let fx = FxRateTable::new();
        let eur = fx.from_gbp(86.0, Currency::EUR);
        assert!((eur - 100.0).abs() < 0.1);
    }

    #[test]
    fn test_fx_cost_gbp_zero_for_gbp() {
        let fx = FxRateTable::new();
        assert!((fx.fx_cost_gbp(10000.0, Currency::GBP)).abs() < 0.001);
    }

    #[test]
    fn test_fx_minimum_fee_dominates_small_positions() {
        let fx = FxRateTable::new();
        // £500 position: spread cost = £0.01, but minimum = £2.00
        let cost = fx.fx_cost_gbp(500.0, Currency::EUR);
        assert!((cost - 2.0).abs() < 0.01);
    }

    #[test]
    fn test_fx_spread_dominates_large_positions() {
        let fx = FxRateTable::new();
        // £500,000 position: spread cost = £10.00, minimum = £2.00
        let cost = fx.fx_cost_gbp(500_000.0, Currency::EUR);
        assert!(cost > 2.0);
        assert!((cost - 10.0).abs() < 0.01);
    }

    #[test]
    fn test_stale_rate_detection() {
        let mut fx = FxRateTable::new();
        assert!(fx.is_stale(1_000_000_000));
        fx.set_rate(Currency::EUR, 0.87, 1_000_000_000);
        assert!(!fx.is_stale(1_000_000_000 + 3_600_000_000_000)); // 1h later
        assert!(fx.is_stale(1_000_000_000 + 90_000_000_000_000)); // 25h later
    }

    #[test]
    fn test_currency_from_str() {
        assert_eq!(Currency::from_str_code("EUR"), Some(Currency::EUR));
        assert_eq!(Currency::from_str_code("gbp"), Some(Currency::GBP));
        assert_eq!(Currency::from_str_code("XYZ"), None);
    }
}
