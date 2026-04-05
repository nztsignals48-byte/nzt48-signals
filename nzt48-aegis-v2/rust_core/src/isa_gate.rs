//! Phase 12: ISA Gate — HMRC ISA eligibility enforcement.
//! Blocks ineligible exchanges, enforces annual deposit limits, provides ETP fallback.

use std::collections::HashSet;

/// Compute the current UK ISA tax year start date as "YYYY-04-06".
/// Tax year runs April 6 to April 5 of the following year.
/// If today is before April 6, the current tax year started the previous calendar year.
pub fn current_isa_tax_year() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let days = secs / 86400;
    let (y, m, d) = days_to_ymd(days as i64);
    let tax_year = if m < 4 || (m == 4 && d < 6) { y - 1 } else { y };
    format!("{}-04-06", tax_year)
}

/// Civil days since Unix epoch to (year, month, day).
/// Algorithm from Howard Hinnant (public domain).
fn days_to_ymd(days: i64) -> (i32, u32, u32) {
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u32;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y as i32, m, d)
}

/// ISA Gate: enforces HMRC rules for Stocks & Shares ISA eligibility.
pub struct IsaGate {
    /// Hard-blocked exchange MICs (not ISA-eligible).
    blocked_exchanges: HashSet<&'static str>,
    /// ISA annual deposit limit (GBP).
    pub annual_limit_gbp: f64,
    /// Deposits this tax year (GBP).
    pub deposits_this_year_gbp: f64,
    /// Current tax year start (YYYY-MM-DD format, e.g., "2026-04-06").
    pub tax_year_start: String,
}

/// Result of ISA eligibility check.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum IsaCheckResult {
    /// Allowed: exchange is ISA-eligible.
    Allowed,
    /// Blocked: exchange is not ISA-eligible. Try ETP fallback.
    BlockedExchange { mic: String, reason: String },
    /// Blocked: annual deposit limit would be exceeded.
    DepositLimitExceeded { remaining_gbp: u64 },
}

impl IsaGate {
    pub fn new(tax_year_start: &str) -> Self {
        let mut blocked = HashSet::new();
        // Hard blocklist: Taiwan, China, India — not HMRC ISA-eligible
        blocked.insert("TWSE"); // Taiwan Stock Exchange
        blocked.insert("XTAI"); // Taiwan (alternate MIC)
        blocked.insert("XSHG"); // Shanghai Stock Exchange
        blocked.insert("XSHE"); // Shenzhen Stock Exchange
        blocked.insert("XBOM"); // BSE (Bombay)
        blocked.insert("XNSE"); // NSE India

        Self {
            blocked_exchanges: blocked,
            annual_limit_gbp: 20_000.0,
            deposits_this_year_gbp: 0.0,
            tax_year_start: tax_year_start.to_string(),
        }
    }

    /// Check if a trade on the given exchange is ISA-eligible.
    pub fn check(&self, exchange_mic: &str, trade_value_gbp: f64) -> IsaCheckResult {
        // Check blocked exchanges
        if self.blocked_exchanges.contains(exchange_mic) {
            return IsaCheckResult::BlockedExchange {
                mic: exchange_mic.to_string(),
                reason: format!("Exchange {exchange_mic} is not ISA-eligible (HMRC rules)"),
            };
        }

        // Check annual deposit limit
        let remaining = self.annual_limit_gbp - self.deposits_this_year_gbp;
        if trade_value_gbp > remaining {
            return IsaCheckResult::DepositLimitExceeded {
                remaining_gbp: remaining.max(0.0) as u64,
            };
        }

        IsaCheckResult::Allowed
    }

    /// Record a deposit against the annual limit.
    pub fn record_deposit(&mut self, amount_gbp: f64) {
        self.deposits_this_year_gbp += amount_gbp;
    }

    /// Remaining ISA allowance.
    pub fn remaining_allowance(&self) -> f64 {
        (self.annual_limit_gbp - self.deposits_this_year_gbp).max(0.0)
    }

    /// Is an exchange blocked?
    pub fn is_blocked(&self, exchange_mic: &str) -> bool {
        self.blocked_exchanges.contains(exchange_mic)
    }

    /// Reset for new tax year (April 6).
    pub fn new_tax_year(&mut self, tax_year_start: &str) {
        self.deposits_this_year_gbp = 0.0;
        self.tax_year_start = tax_year_start.to_string();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_allowed_european_exchanges() {
        let gate = IsaGate::new("2026-04-06");
        assert_eq!(gate.check("XLON", 1000.0), IsaCheckResult::Allowed);
        assert_eq!(gate.check("XETR", 1000.0), IsaCheckResult::Allowed);
        assert_eq!(gate.check("XPAR", 1000.0), IsaCheckResult::Allowed);
        assert_eq!(gate.check("XAMS", 1000.0), IsaCheckResult::Allowed);
    }

    #[test]
    fn test_blocked_taiwan() {
        let gate = IsaGate::new("2026-04-06");
        match gate.check("TWSE", 1000.0) {
            IsaCheckResult::BlockedExchange { mic, .. } => assert_eq!(mic, "TWSE"),
            other => panic!("Expected BlockedExchange, got {other:?}"),
        }
        match gate.check("XTAI", 1000.0) {
            IsaCheckResult::BlockedExchange { mic, .. } => assert_eq!(mic, "XTAI"),
            other => panic!("Expected BlockedExchange, got {other:?}"),
        }
    }

    #[test]
    fn test_blocked_china() {
        let gate = IsaGate::new("2026-04-06");
        assert!(gate.is_blocked("XSHG"));
        assert!(gate.is_blocked("XSHE"));
    }

    #[test]
    fn test_blocked_india() {
        let gate = IsaGate::new("2026-04-06");
        assert!(gate.is_blocked("XBOM"));
        assert!(gate.is_blocked("XNSE"));
    }

    #[test]
    fn test_deposit_limit() {
        let mut gate = IsaGate::new("2026-04-06");
        gate.record_deposit(18_000.0);
        assert!((gate.remaining_allowance() - 2_000.0).abs() < 0.01);

        // £3000 trade exceeds remaining £2000
        match gate.check("XLON", 3000.0) {
            IsaCheckResult::DepositLimitExceeded { remaining_gbp } => {
                assert_eq!(remaining_gbp, 2000);
            }
            other => panic!("Expected DepositLimitExceeded, got {other:?}"),
        }

        // £1500 trade is fine
        assert_eq!(gate.check("XLON", 1500.0), IsaCheckResult::Allowed);
    }

    #[test]
    fn test_new_tax_year_resets() {
        let mut gate = IsaGate::new("2025-04-06");
        gate.record_deposit(15_000.0);
        assert!((gate.remaining_allowance() - 5_000.0).abs() < 0.01);

        gate.new_tax_year("2026-04-06");
        assert!((gate.remaining_allowance() - 20_000.0).abs() < 0.01);
        assert_eq!(gate.tax_year_start, "2026-04-06");
    }

    #[test]
    fn test_tsmc_case_study() {
        // AT-143: TSMC blocked via TWSE direct, but allowed via TSM3.L ETP on LSE
        let gate = IsaGate::new("2026-04-06");
        assert!(gate.is_blocked("TWSE")); // Direct TSMC: blocked
        assert!(!gate.is_blocked("XLON")); // TSM3.L on LSE: allowed
    }
}
