//! Phase 19: Asia-Pacific Mode A infrastructure.
//! 6 Asian exchanges with lunch break detection and ISA eligibility checking.

use crate::isa_gate::IsaGate;

/// Mode A window boundaries (UTC seconds from midnight).
/// 23:00-08:00 UTC spans midnight, so we check both ranges.
const MODE_A_START_UTC_SECS: u32 = 23 * 3600; // 23:00 UTC
const MODE_A_END_UTC_SECS: u32 = 8 * 3600; // 08:00 UTC

/// An Asia-Pacific exchange profile with lunch break support.
#[derive(Clone, Debug)]
pub struct AsianExchangeProfile {
    /// MIC code (ISO 10383).
    pub mic: &'static str,
    /// Human-readable name.
    pub name: &'static str,
    /// Trading currency (ISO 4217).
    pub currency: &'static str,
    /// Continuous trading start (UTC seconds from midnight).
    pub open_utc_secs: u32,
    /// Continuous trading end (UTC seconds from midnight).
    pub close_utc_secs: u32,
    /// Lunch break start (UTC seconds from midnight). 0 = no lunch break.
    pub lunch_start_utc_secs: u32,
    /// Lunch break end (UTC seconds from midnight). 0 = no lunch break.
    pub lunch_end_utc_secs: u32,
    /// Country code (ISO 3166-1 alpha-2).
    pub country: &'static str,
}

impl AsianExchangeProfile {
    /// Is the exchange in its lunch break at the given UTC time?
    pub fn is_lunch_break(&self, utc_secs: u32) -> bool {
        if self.lunch_start_utc_secs == 0 && self.lunch_end_utc_secs == 0 {
            return false;
        }
        utc_secs >= self.lunch_start_utc_secs && utc_secs < self.lunch_end_utc_secs
    }

    /// Is the exchange open (continuous trading, excluding lunch) at the given UTC time?
    /// Handles midnight-crossing sessions (e.g., NZX opens at 21:00 UTC).
    pub fn is_open(&self, utc_secs: u32) -> bool {
        if self.is_lunch_break(utc_secs) {
            return false;
        }
        if self.open_utc_secs <= self.close_utc_secs {
            // Same-day session
            utc_secs >= self.open_utc_secs && utc_secs < self.close_utc_secs
        } else {
            // Midnight-crossing session
            utc_secs >= self.open_utc_secs || utc_secs < self.close_utc_secs
        }
    }
}

/// Asia-Pacific session manager tracking Mode A exchanges.
pub struct AsianSession {
    profiles: Vec<AsianExchangeProfile>,
}

impl AsianSession {
    /// Create a new Asian session with the 6 default exchange profiles.
    pub fn new() -> Self {
        Self {
            profiles: build_asian_profiles(),
        }
    }

    /// Is the Mode A window active? (23:00-08:00 UTC, crosses midnight)
    pub fn is_mode_a(&self, utc_secs: u32) -> bool {
        !(MODE_A_END_UTC_SECS..MODE_A_START_UTC_SECS).contains(&utc_secs)
    }

    /// Is a specific exchange in its lunch break?
    /// Returns false if MIC not found.
    pub fn is_lunch_break(&self, mic: &str, utc_secs: u32) -> bool {
        self.by_mic(mic).is_some_and(|p| p.is_lunch_break(utc_secs))
    }

    /// Is a specific exchange open (excluding lunch)?
    /// Returns false if MIC not found.
    pub fn is_exchange_open(&self, mic: &str, utc_secs: u32) -> bool {
        self.by_mic(mic).is_some_and(|p| p.is_open(utc_secs))
    }

    /// Is this exchange ISA-eligible? Delegates to IsaGate blocklist.
    /// Taiwan (TWSE/XTAI), China (XSHG/XSHE), India (XBOM/XNSE) are blocked.
    pub fn isa_eligible(&self, mic: &str, isa_gate: &IsaGate) -> bool {
        !isa_gate.is_blocked(mic)
    }

    /// Returns MIC codes of all exchanges currently open.
    pub fn open_exchanges(&self, utc_secs: u32) -> Vec<&str> {
        self.profiles
            .iter()
            .filter(|p| p.is_open(utc_secs))
            .map(|p| p.mic)
            .collect()
    }

    /// Look up an exchange by MIC code.
    pub fn by_mic(&self, mic: &str) -> Option<&AsianExchangeProfile> {
        self.profiles.iter().find(|p| p.mic == mic)
    }

    /// All exchange profiles.
    pub fn all(&self) -> &[AsianExchangeProfile] {
        &self.profiles
    }

    /// Number of registered Asian exchanges.
    pub fn len(&self) -> usize {
        self.profiles.len()
    }

    /// Is the registry empty?
    pub fn is_empty(&self) -> bool {
        self.profiles.is_empty()
    }
}

impl Default for AsianSession {
    fn default() -> Self {
        Self::new()
    }
}

/// Build the 6 Asia-Pacific exchange profiles.
fn build_asian_profiles() -> Vec<AsianExchangeProfile> {
    vec![
        // Japan — Tokyo Stock Exchange
        AsianExchangeProfile {
            mic: "XTKS",
            name: "Tokyo Stock Exchange",
            currency: "JPY",
            open_utc_secs: 0,       // 00:00 UTC (09:00 JST)
            close_utc_secs: 6 * 3600,       // 06:00 UTC (15:00 JST)
            lunch_start_utc_secs: 2 * 3600 + 30 * 60, // 02:30 UTC (11:30 JST)
            lunch_end_utc_secs: 3 * 3600 + 30 * 60,   // 03:30 UTC (12:30 JST)
            country: "JP",
        },
        // Hong Kong — HKEX
        AsianExchangeProfile {
            mic: "XHKG",
            name: "Hong Kong Exchanges",
            currency: "HKD",
            open_utc_secs: 3600 + 30 * 60, // 01:30 UTC (09:30 HKT)
            close_utc_secs: 8 * 3600,           // 08:00 UTC (16:00 HKT)
            lunch_start_utc_secs: 4 * 3600,     // 04:00 UTC (12:00 HKT)
            lunch_end_utc_secs: 5 * 3600,       // 05:00 UTC (13:00 HKT)
            country: "HK",
        },
        // Australia — ASX
        AsianExchangeProfile {
            mic: "XASX",
            name: "Australian Securities Exchange",
            currency: "AUD",
            open_utc_secs: 0,       // 00:00 UTC (10:00 AEST)
            close_utc_secs: 6 * 3600,       // 06:00 UTC (16:00 AEST)
            lunch_start_utc_secs: 0,         // No lunch break
            lunch_end_utc_secs: 0,
            country: "AU",
        },
        // Singapore — SGX
        AsianExchangeProfile {
            mic: "XSES",
            name: "Singapore Exchange",
            currency: "SGD",
            open_utc_secs: 3600,       // 01:00 UTC (09:00 SGT)
            close_utc_secs: 9 * 3600,       // 09:00 UTC (17:00 SGT)
            lunch_start_utc_secs: 0,         // No lunch break (removed 2011)
            lunch_end_utc_secs: 0,
            country: "SG",
        },
        // South Korea — KRX
        AsianExchangeProfile {
            mic: "XKRX",
            name: "Korea Exchange",
            currency: "KRW",
            open_utc_secs: 0,       // 00:00 UTC (09:00 KST)
            close_utc_secs: 6 * 3600 + 30 * 60, // 06:30 UTC (15:30 KST)
            lunch_start_utc_secs: 0,         // No lunch break (removed 2000)
            lunch_end_utc_secs: 0,
            country: "KR",
        },
        // New Zealand — NZX
        AsianExchangeProfile {
            mic: "XNZE",
            name: "New Zealand Exchange",
            currency: "NZD",
            open_utc_secs: 21 * 3600,      // 21:00 UTC (10:00 NZDT)
            close_utc_secs: 4 * 3600 + 45 * 60, // 04:45 UTC (17:45 NZDT)
            lunch_start_utc_secs: 0,         // No lunch break
            lunch_end_utc_secs: 0,
            country: "NZ",
        },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mode_a_window() {
        let session = AsianSession::new();
        // 23:00 UTC is Mode A start
        assert!(session.is_mode_a(23 * 3600));
        // 23:30 UTC
        assert!(session.is_mode_a(23 * 3600 + 30 * 60));
        // 02:00 UTC (after midnight, still Mode A)
        assert!(session.is_mode_a(2 * 3600));
        // 07:59 UTC (still Mode A)
        assert!(session.is_mode_a(7 * 3600 + 59 * 60));
        // 08:00 UTC (Mode A ends)
        assert!(!session.is_mode_a(8 * 3600));
        // 12:00 UTC (well outside Mode A)
        assert!(!session.is_mode_a(12 * 3600));
    }

    #[test]
    fn test_tse_lunch_break() {
        let session = AsianSession::new();
        // TSE lunch: 02:30-03:30 UTC
        assert!(!session.is_lunch_break("XTKS", 2 * 3600 + 29 * 60));
        assert!(session.is_lunch_break("XTKS", 2 * 3600 + 30 * 60));
        assert!(session.is_lunch_break("XTKS", 3 * 3600));
        assert!(!session.is_lunch_break("XTKS", 3 * 3600 + 30 * 60));
    }

    #[test]
    fn test_hkex_lunch_break() {
        let session = AsianSession::new();
        // HKEX lunch: 04:00-05:00 UTC
        assert!(!session.is_lunch_break("XHKG", 3 * 3600 + 59 * 60));
        assert!(session.is_lunch_break("XHKG", 4 * 3600));
        assert!(session.is_lunch_break("XHKG", 4 * 3600 + 30 * 60));
        assert!(!session.is_lunch_break("XHKG", 5 * 3600));
    }

    #[test]
    fn test_tse_open_excludes_lunch() {
        let session = AsianSession::new();
        // TSE: 00:00-06:00 UTC, lunch 02:30-03:30
        assert!(session.is_exchange_open("XTKS", 3600)); // morning session
        assert!(!session.is_exchange_open("XTKS", 3 * 3600)); // lunch
        assert!(session.is_exchange_open("XTKS", 4 * 3600)); // afternoon session
        assert!(!session.is_exchange_open("XTKS", 6 * 3600)); // after close
    }

    #[test]
    fn test_nzx_midnight_crossing() {
        let session = AsianSession::new();
        // NZX: 21:00 UTC - 04:45 UTC (crosses midnight)
        assert!(!session.is_exchange_open("XNZE", 20 * 3600 + 59 * 60)); // before open
        assert!(session.is_exchange_open("XNZE", 21 * 3600));            // at open
        assert!(session.is_exchange_open("XNZE", 23 * 3600));            // pre-midnight
        assert!(session.is_exchange_open("XNZE", 2 * 3600));             // post-midnight
        assert!(!session.is_exchange_open("XNZE", 4 * 3600 + 45 * 60)); // at close
    }

    #[test]
    fn test_isa_eligibility() {
        let session = AsianSession::new();
        let isa_gate = IsaGate::new("2026-04-06");
        // TSE (Japan), ASX (Australia), SGX (Singapore) should be ISA-eligible
        assert!(session.isa_eligible("XTKS", &isa_gate));
        assert!(session.isa_eligible("XASX", &isa_gate));
        assert!(session.isa_eligible("XSES", &isa_gate));
        // Taiwan and China are blocked
        assert!(!session.isa_eligible("TWSE", &isa_gate));
        assert!(!session.isa_eligible("XSHG", &isa_gate));
    }

    #[test]
    fn test_no_lunch_exchanges() {
        let session = AsianSession::new();
        // ASX, SGX, KRX, NZX have no lunch break
        for mic in &["XASX", "XSES", "XKRX", "XNZE"] {
            assert!(!session.is_lunch_break(mic, 3 * 3600));
        }
    }

    #[test]
    fn test_registry_has_6_exchanges() {
        let session = AsianSession::new();
        assert_eq!(session.len(), 6);
        assert!(!session.is_empty());
    }

    #[test]
    fn test_unknown_mic() {
        let session = AsianSession::new();
        assert!(!session.is_exchange_open("FAKE", 3 * 3600));
        assert!(!session.is_lunch_break("FAKE", 3 * 3600));
        assert!(session.by_mic("FAKE").is_none());
    }
}
