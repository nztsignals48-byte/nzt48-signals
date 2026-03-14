//! Phase 18: European equities session manager.
//! Extends Mode B for 15 European exchanges with their specific trading hours.

use crate::exchange_profile::ExchangeRegistry;

/// Mode B window boundaries (UTC seconds from midnight).
const MODE_B_OPEN_UTC_SECS: u32 = 7 * 3600; // 07:00 UTC (earliest EU open)
const MODE_B_CLOSE_UTC_SECS: u32 = 16 * 3600 + 30 * 60; // 16:30 UTC (latest EU close)

/// European session manager tracking open/close state of all 15 exchanges.
pub struct EuropeanSession {
    registry: ExchangeRegistry,
}

impl EuropeanSession {
    /// Create a new European session manager with the default exchange registry.
    pub fn new() -> Self {
        Self {
            registry: ExchangeRegistry::new(),
        }
    }

    /// Create from an existing registry.
    pub fn with_registry(registry: ExchangeRegistry) -> Self {
        Self { registry }
    }

    /// Is the Mode B window active? (07:00-16:30 UTC)
    pub fn is_mode_b(&self, utc_secs: u32) -> bool {
        (MODE_B_OPEN_UTC_SECS..MODE_B_CLOSE_UTC_SECS).contains(&utc_secs)
    }

    /// Is a specific exchange open at the given UTC time?
    /// Returns false if the MIC is not found in the registry.
    pub fn is_exchange_open(&self, mic: &str, utc_secs: u32) -> bool {
        self.registry
            .by_mic(mic)
            .is_some_and(|p| p.is_open(utc_secs))
    }

    /// Returns the MIC codes of all exchanges currently open at the given UTC time.
    pub fn open_exchanges(&self, utc_secs: u32) -> Vec<&str> {
        self.registry
            .all()
            .iter()
            .filter(|p| p.is_open(utc_secs))
            .map(|p| p.mic)
            .collect()
    }

    /// Number of exchanges currently open.
    pub fn open_count(&self, utc_secs: u32) -> usize {
        self.registry
            .all()
            .iter()
            .filter(|p| p.is_open(utc_secs))
            .count()
    }

    /// Next exchange close time (UTC seconds from midnight) after `utc_secs`.
    /// Returns `None` if no exchanges are currently open.
    pub fn next_close_utc_secs(&self, utc_secs: u32) -> Option<u32> {
        self.registry
            .all()
            .iter()
            .filter(|p| p.is_open(utc_secs))
            .map(|p| p.close_utc_secs)
            .min()
    }

    /// Latest close time across all exchanges (UTC seconds from midnight).
    /// Useful for knowing when Mode B fully ends.
    pub fn latest_close_utc_secs(&self) -> u32 {
        self.registry
            .all()
            .iter()
            .map(|p| p.close_utc_secs)
            .max()
            .unwrap_or(MODE_B_CLOSE_UTC_SECS)
    }

    /// Is the given exchange in its closing auction window?
    pub fn is_closing_auction(&self, mic: &str, utc_secs: u32) -> bool {
        self.registry
            .by_mic(mic)
            .is_some_and(|p| p.is_closing_auction(utc_secs))
    }

    /// Entry gate: is entry allowed on this exchange right now?
    /// Blocks entry during closing auction and outside trading hours.
    pub fn entry_allowed(&self, mic: &str, utc_secs: u32) -> bool {
        let Some(profile) = self.registry.by_mic(mic) else {
            return false;
        };
        profile.is_open(utc_secs) && !profile.is_closing_auction(utc_secs)
    }

    /// Access the underlying registry.
    pub fn registry(&self) -> &ExchangeRegistry {
        &self.registry
    }
}

impl Default for EuropeanSession {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mode_b_window() {
        let session = EuropeanSession::new();
        // Before Mode B
        assert!(!session.is_mode_b(6 * 3600 + 59 * 60));
        // Mode B start
        assert!(session.is_mode_b(7 * 3600));
        // Midday
        assert!(session.is_mode_b(12 * 3600));
        // Mode B end (exclusive)
        assert!(!session.is_mode_b(16 * 3600 + 30 * 60));
    }

    #[test]
    fn test_exchange_open_xlon() {
        let session = EuropeanSession::new();
        // XLON opens at 08:00, closes at 16:30
        assert!(!session.is_exchange_open("XLON", 7 * 3600 + 59 * 60));
        assert!(session.is_exchange_open("XLON", 8 * 3600));
        assert!(session.is_exchange_open("XLON", 12 * 3600));
        assert!(!session.is_exchange_open("XLON", 16 * 3600 + 30 * 60));
    }

    #[test]
    fn test_unknown_mic_returns_false() {
        let session = EuropeanSession::new();
        assert!(!session.is_exchange_open("FAKE", 12 * 3600));
        assert!(!session.entry_allowed("FAKE", 12 * 3600));
    }

    #[test]
    fn test_open_exchanges_midday() {
        let session = EuropeanSession::new();
        // At 12:00 UTC all 15 exchanges should be open
        let open = session.open_exchanges(12 * 3600);
        assert_eq!(open.len(), 15);
        assert_eq!(session.open_count(12 * 3600), 15);
    }

    #[test]
    fn test_open_exchanges_early_morning() {
        let session = EuropeanSession::new();
        // At 07:30 UTC: XLON and XDUB not yet open (08:00), but continental exchanges open at 07:00
        let open = session.open_exchanges(7 * 3600 + 30 * 60);
        assert!(!open.contains(&"XLON"));
        assert!(!open.contains(&"XDUB"));
        // Continental exchanges should be open
        assert!(open.contains(&"XETR"));
        assert!(open.contains(&"XPAR"));
    }

    #[test]
    fn test_next_close() {
        let session = EuropeanSession::new();
        // At 12:00 UTC, nearest close is Oslo at 14:20
        let next = session.next_close_utc_secs(12 * 3600);
        assert_eq!(next, Some(14 * 3600 + 20 * 60)); // XOSL 14:20
    }

    #[test]
    fn test_entry_blocked_during_auction() {
        let session = EuropeanSession::new();
        // XETR closing auction starts at 15:30
        // Just before close: is_open is true but closing auction is also true
        assert!(!session.entry_allowed("XETR", 15 * 3600 + 31 * 60));
    }

    #[test]
    fn test_latest_close() {
        let session = EuropeanSession::new();
        // XLON and XDUB close at 16:30 UTC = 59400
        assert_eq!(session.latest_close_utc_secs(), 16 * 3600 + 30 * 60);
    }
}
