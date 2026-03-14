//! Clock synchronisation, LSE market hours, UK holiday calendar.
//! All times are seconds-from-midnight London local time (H109).
//! Timezone conversion (chrono-tz) happens at the binary entrypoint;
//! this module works with pre-computed London seconds for testability.

/// LSE open time (seconds from midnight London).
pub const LSE_OPEN_SECS: u32 = 8 * 3600; // 08:00
/// LSE close time.
pub const LSE_CLOSE_SECS: u32 = 16 * 3600 + 30 * 60; // 16:30
/// Entry cutoff (H35).
pub const ENTRY_CUTOFF_SECS: u32 = 15 * 3600 + 45 * 60; // 15:45
/// Auction open start (07:50).
pub const AUCTION_OPEN_START: u32 = 7 * 3600 + 50 * 60;
/// Auction open end (08:00).
pub const AUCTION_OPEN_END: u32 = 8 * 3600;
/// Auction close start (16:30).
pub const AUCTION_CLOSE_START: u32 = 16 * 3600 + 30 * 60;
/// Auction close end (16:35).
pub const AUCTION_CLOSE_END: u32 = 16 * 3600 + 35 * 60;
/// EOD flatten phases.
pub const EOD_PHASE1_SECS: u32 = 15 * 3600 + 55 * 60; // T-35
pub const EOD_PHASE2_SECS: u32 = 16 * 3600 + 15 * 60; // T-15
pub const EOD_PHASE3_SECS: u32 = 16 * 3600 + 25 * 60; // T-5

/// Phase 11: 5-mode trading clock.
/// Each mode has different subscription, signal, and execution rules.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TradingMode {
    /// LSE Pre-open: 07:00-08:00 London. Pre-market data, no entries.
    ModeA,
    /// LSE Regular: 08:00-16:30 London. Full trading.
    ModeB,
    /// LSE Extended: 16:30-17:00 London. Exit-only, no new entries.
    ModeBPlus,
    /// LSE Post-close: 17:00-20:00 London. Reporting and analytics only.
    ModeC,
    /// Dark: 20:00-07:00 London. Ouroboros nightly runs. No market access.
    Dark,
}

impl TradingMode {
    /// Determine the current trading mode from London seconds-from-midnight.
    pub fn from_london_secs(time_secs: u32) -> Self {
        const MODE_A_START: u32 = 7 * 3600;         // 07:00
        const MODE_B_START: u32 = 8 * 3600;          // 08:00
        const MODE_B_PLUS_START: u32 = 16 * 3600 + 30 * 60; // 16:30
        const MODE_C_START: u32 = 17 * 3600;          // 17:00
        const DARK_START: u32 = 20 * 3600;            // 20:00

        match time_secs {
            t if !(MODE_A_START..DARK_START).contains(&t) => TradingMode::Dark,
            t if t >= MODE_C_START => TradingMode::ModeC,
            t if t >= MODE_B_PLUS_START => TradingMode::ModeBPlus,
            t if t >= MODE_B_START => TradingMode::ModeB,
            _ => TradingMode::ModeA,
        }
    }

    /// Can new entries be submitted in this mode?
    pub fn allows_entries(&self) -> bool {
        matches!(self, TradingMode::ModeB)
    }

    /// Can exits be executed in this mode?
    pub fn allows_exits(&self) -> bool {
        matches!(self, TradingMode::ModeB | TradingMode::ModeBPlus)
    }

    /// Should market data subscriptions be active?
    pub fn requires_market_data(&self) -> bool {
        matches!(
            self,
            TradingMode::ModeA | TradingMode::ModeB | TradingMode::ModeBPlus
        )
    }
}

/// IBKR clock synchronisation + LSE calendar.
#[derive(Clone, Debug)]
pub struct Clock {
    /// Broker time minus system time (nanoseconds). Positive = broker ahead.
    offset_ns: i64,
    /// UK bank holidays in "YYYY-MM-DD" format for fast string comparison.
    holidays: Vec<String>,
    /// Whether clock has been synced with broker.
    synced: bool,
}

impl Clock {
    pub fn new(holidays: Vec<String>) -> Self {
        Self {
            offset_ns: 0,
            holidays,
            synced: false,
        }
    }

    /// Sync local clock with IBKR reqCurrentTime() result.
    /// `broker_time_secs` is Unix epoch seconds from IBKR.
    /// `system_time_ns` is current system time in nanoseconds.
    pub fn sync(&mut self, broker_time_secs: u64, system_time_ns: u64) {
        let broker_ns = broker_time_secs * 1_000_000_000;
        self.offset_ns = broker_ns as i64 - system_time_ns as i64;
        self.synced = true;
    }

    pub fn offset_ns(&self) -> i64 {
        self.offset_ns
    }

    pub fn is_synced(&self) -> bool {
        self.synced
    }

    /// Offset in seconds (for logging).
    pub fn offset_secs(&self) -> f64 {
        self.offset_ns as f64 / 1_000_000_000.0
    }

    /// Is the LSE currently in continuous trading? (08:00-16:30 London)
    pub fn is_lse_open(time_secs: u32) -> bool {
        (LSE_OPEN_SECS..LSE_CLOSE_SECS).contains(&time_secs)
    }

    /// Is the current time within an auction period?
    pub fn is_auction(time_secs: u32) -> bool {
        (AUCTION_OPEN_START..AUCTION_OPEN_END).contains(&time_secs)
            || (AUCTION_CLOSE_START..AUCTION_CLOSE_END).contains(&time_secs)
    }

    /// Is the current time past the entry cutoff? (15:45 London, H35)
    pub fn is_after_cutoff(time_secs: u32) -> bool {
        time_secs >= ENTRY_CUTOFF_SECS
    }

    /// Which EOD flatten phase are we in? None if before T-35.
    pub fn eod_phase(time_secs: u32) -> Option<u8> {
        if time_secs >= EOD_PHASE3_SECS {
            Some(3) // T-5: MTL emergency
        } else if time_secs >= EOD_PHASE2_SECS {
            Some(2) // T-15: limit at mid
        } else if time_secs >= EOD_PHASE1_SECS {
            Some(1) // T-35: passive limit
        } else {
            None
        }
    }

    /// Is the given date (YYYY-MM-DD) a UK bank holiday?
    pub fn is_uk_holiday(&self, date: &str) -> bool {
        self.holidays.iter().any(|h| h == date)
    }

    /// Should we trade today? Market is open AND not a holiday.
    pub fn is_trading_day(&self, date: &str, time_secs: u32) -> bool {
        !self.is_uk_holiday(date) && Self::is_lse_open(time_secs)
    }

    /// Compute current London seconds-from-midnight from system nanoseconds.
    /// Applies broker clock offset and BST adjustment.
    /// P1-01 FIX: Hardcoded exact BST transition dates (last Sunday of March/October)
    /// instead of day-of-year approximation that was off by ±3 days.
    pub fn now_london_secs(&self, system_ns: u64) -> u32 {
        // Apply broker clock offset to get "real" time
        let adjusted_ns = if self.offset_ns >= 0 {
            system_ns.wrapping_add(self.offset_ns as u64)
        } else {
            system_ns.wrapping_sub((-self.offset_ns) as u64)
        };
        let epoch_secs = adjusted_ns / 1_000_000_000;

        // UTC seconds from midnight
        let utc_secs_from_midnight = (epoch_secs % 86400) as u32;

        let is_bst = Self::is_bst_from_epoch(epoch_secs);

        if is_bst {
            // BST = UTC + 1 hour
            (utc_secs_from_midnight + 3600) % 86400
        } else {
            utc_secs_from_midnight
        }
    }

    /// Check if a Unix epoch timestamp falls within BST (British Summer Time).
    /// BST starts: last Sunday of March at 01:00 UTC.
    /// BST ends: last Sunday of October at 01:00 UTC.
    /// Hardcoded for 2025-2028 (update annually or when extending beyond 2028).
    fn is_bst_from_epoch(epoch_secs: u64) -> bool {
        // BST transition Unix timestamps (all at 01:00 UTC on the transition day):
        // 2025 starts Jan 1 00:00 UTC = 1735689600
        // 2029 starts Jan 1 00:00 UTC = 1861920000
        const YEAR_2025_START: u64 = 1_735_689_600;
        const YEAR_2029_START: u64 = 1_861_920_000;

        const BST_RANGES: [(u64, u64); 4] = [
            (1_743_296_400, 1_761_440_400), // 2025: Mar 30 01:00 UTC → Oct 26 01:00 UTC
            (1_774_746_000, 1_792_890_000), // 2026: Mar 29 01:00 UTC → Oct 25 01:00 UTC
            (1_806_195_600, 1_824_944_400), // 2027: Mar 28 01:00 UTC → Oct 31 01:00 UTC
            (1_837_645_200, 1_856_394_000), // 2028: Mar 26 01:00 UTC → Oct 29 01:00 UTC
        ];

        // For dates within 2025-2028, use exact transition timestamps
        if (YEAR_2025_START..YEAR_2029_START).contains(&epoch_secs) {
            for &(start, end) in &BST_RANGES {
                if epoch_secs >= start && epoch_secs < end {
                    return true;
                }
            }
            return false; // Within 2025-2028 but not in any BST range
        }

        // Fallback for dates outside 2025-2028: use day-of-year approximation
        let day_of_year = ((epoch_secs / 86400) % 365) as u32;
        (84..301).contains(&day_of_year)
    }

    /// Fraction of trading day elapsed [0.0, 1.0].
    /// 08:00 = 0.0, 16:30 = 1.0. Clamps outside trading hours.
    pub fn time_of_day_fraction(time_secs: u32) -> f64 {
        let trading_duration = LSE_CLOSE_SECS - LSE_OPEN_SECS; // 8.5 hours
        if time_secs <= LSE_OPEN_SECS {
            return 0.0;
        }
        if time_secs >= LSE_CLOSE_SECS {
            return 1.0;
        }
        (time_secs - LSE_OPEN_SECS) as f64 / trading_duration as f64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lse_open_hours() {
        assert!(!Clock::is_lse_open(7 * 3600)); // 07:00
        assert!(Clock::is_lse_open(8 * 3600)); // 08:00
        assert!(Clock::is_lse_open(12 * 3600)); // 12:00
        assert!(Clock::is_lse_open(16 * 3600 + 29 * 60)); // 16:29
        assert!(!Clock::is_lse_open(16 * 3600 + 30 * 60)); // 16:30
    }

    #[test]
    fn test_auction_periods() {
        assert!(Clock::is_auction(7 * 3600 + 50 * 60)); // 07:50
        assert!(Clock::is_auction(7 * 3600 + 55 * 60)); // 07:55
        assert!(!Clock::is_auction(8 * 3600)); // 08:00
        assert!(Clock::is_auction(16 * 3600 + 30 * 60)); // 16:30
        assert!(Clock::is_auction(16 * 3600 + 34 * 60)); // 16:34
        assert!(!Clock::is_auction(16 * 3600 + 35 * 60)); // 16:35
    }

    #[test]
    fn test_entry_cutoff() {
        assert!(!Clock::is_after_cutoff(15 * 3600 + 44 * 60)); // 15:44
        assert!(Clock::is_after_cutoff(15 * 3600 + 45 * 60)); // 15:45
    }

    #[test]
    fn test_eod_phases() {
        assert_eq!(Clock::eod_phase(15 * 3600), None);
        assert_eq!(Clock::eod_phase(15 * 3600 + 55 * 60), Some(1));
        assert_eq!(Clock::eod_phase(16 * 3600 + 15 * 60), Some(2));
        assert_eq!(Clock::eod_phase(16 * 3600 + 25 * 60), Some(3));
    }

    #[test]
    fn test_clock_sync() {
        let mut clock = Clock::new(vec![]);
        assert!(!clock.is_synced());
        // Broker says 1_000_000 seconds, system is at 1_000_000_000_000_000 ns
        clock.sync(1_000_000, 1_000_000_000_000_000);
        assert!(clock.is_synced());
        // offset = (1_000_000 * 1e9) - 1_000_000_000_000_000 = 0
        assert_eq!(clock.offset_ns(), 0);
    }

    #[test]
    fn test_clock_sync_with_offset() {
        let mut clock = Clock::new(vec![]);
        // Broker 2s ahead: broker_time = 100s, system = 98_000_000_000 ns
        clock.sync(100, 98_000_000_000);
        assert_eq!(clock.offset_ns(), 2_000_000_000); // +2s
        assert!((clock.offset_secs() - 2.0).abs() < 0.001);
    }

    #[test]
    fn test_uk_holidays() {
        let clock = Clock::new(vec!["2026-01-01".to_string(), "2026-12-25".to_string()]);
        assert!(clock.is_uk_holiday("2026-01-01"));
        assert!(clock.is_uk_holiday("2026-12-25"));
        assert!(!clock.is_uk_holiday("2026-03-09"));
    }

    #[test]
    fn test_trading_day() {
        let clock = Clock::new(vec!["2026-12-25".to_string()]);
        assert!(clock.is_trading_day("2026-03-09", 10 * 3600)); // Normal day
        assert!(!clock.is_trading_day("2026-12-25", 10 * 3600)); // Holiday
        assert!(!clock.is_trading_day("2026-03-09", 7 * 3600)); // Before open
    }

    // ── P1-01: BST transition tests ──
    #[test]
    fn test_bst_2026_transition() {
        // 2026 BST starts: Mar 29 at 01:00 UTC = 1774746000
        // Just before BST: Mar 29 00:59 UTC
        assert!(!Clock::is_bst_from_epoch(1_774_746_000 - 60));
        // BST start: Mar 29 01:00 UTC
        assert!(Clock::is_bst_from_epoch(1_774_746_000));
        // Mid-summer: definitely BST
        assert!(Clock::is_bst_from_epoch(1_774_746_000 + 86400 * 90));
        // Just before BST ends: Oct 25 00:59 UTC = 1792890000 - 60
        assert!(Clock::is_bst_from_epoch(1_792_890_000 - 60));
        // BST ends: Oct 25 01:00 UTC
        assert!(!Clock::is_bst_from_epoch(1_792_890_000));
    }

    #[test]
    fn test_bst_london_secs_shift() {
        let clock = Clock::new(vec![]);
        // During BST: 07:00 UTC should become 08:00 London
        // Mar 30 2026 07:00 UTC = 1774746000 + 86400 (Mar 30) + 3600*7 (07:00)
        // Mar 30 = Mar 29 + 1 day. BST start epoch is Mar 29 01:00.
        // Mar 30 07:00 UTC = 1774746000 + 86400 + 6*3600 = 1774746000 + 86400 + 21600
        let bst_epoch_ns = (1_774_746_000u64 + 86400 + 6 * 3600) * 1_000_000_000;
        let london_secs = clock.now_london_secs(bst_epoch_ns);
        // 07:00 UTC + 1hr BST = 08:00 London = 28800
        assert_eq!(london_secs, 8 * 3600, "7:00 UTC during BST = 8:00 London");
    }

    // ── Phase 11: TradingMode tests ──
    #[test]
    fn test_trading_mode_transitions() {
        // Dark: 20:00-07:00
        assert_eq!(TradingMode::from_london_secs(0), TradingMode::Dark);
        assert_eq!(TradingMode::from_london_secs(3 * 3600), TradingMode::Dark);
        assert_eq!(TradingMode::from_london_secs(6 * 3600 + 59 * 60), TradingMode::Dark);

        // ModeA: 07:00-08:00
        assert_eq!(TradingMode::from_london_secs(7 * 3600), TradingMode::ModeA);
        assert_eq!(TradingMode::from_london_secs(7 * 3600 + 30 * 60), TradingMode::ModeA);
        assert_eq!(TradingMode::from_london_secs(7 * 3600 + 59 * 60), TradingMode::ModeA);

        // ModeB: 08:00-16:30
        assert_eq!(TradingMode::from_london_secs(8 * 3600), TradingMode::ModeB);
        assert_eq!(TradingMode::from_london_secs(12 * 3600), TradingMode::ModeB);
        assert_eq!(TradingMode::from_london_secs(16 * 3600 + 29 * 60), TradingMode::ModeB);

        // ModeBPlus: 16:30-17:00
        assert_eq!(TradingMode::from_london_secs(16 * 3600 + 30 * 60), TradingMode::ModeBPlus);
        assert_eq!(TradingMode::from_london_secs(16 * 3600 + 45 * 60), TradingMode::ModeBPlus);
        assert_eq!(TradingMode::from_london_secs(16 * 3600 + 59 * 60), TradingMode::ModeBPlus);

        // ModeC: 17:00-20:00
        assert_eq!(TradingMode::from_london_secs(17 * 3600), TradingMode::ModeC);
        assert_eq!(TradingMode::from_london_secs(19 * 3600), TradingMode::ModeC);
        assert_eq!(TradingMode::from_london_secs(19 * 3600 + 59 * 60), TradingMode::ModeC);

        // Dark again: 20:00+
        assert_eq!(TradingMode::from_london_secs(20 * 3600), TradingMode::Dark);
        assert_eq!(TradingMode::from_london_secs(23 * 3600), TradingMode::Dark);
    }

    #[test]
    fn test_trading_mode_entry_rules() {
        assert!(!TradingMode::ModeA.allows_entries());
        assert!(TradingMode::ModeB.allows_entries());
        assert!(!TradingMode::ModeBPlus.allows_entries());
        assert!(!TradingMode::ModeC.allows_entries());
        assert!(!TradingMode::Dark.allows_entries());
    }

    #[test]
    fn test_trading_mode_exit_rules() {
        assert!(!TradingMode::ModeA.allows_exits());
        assert!(TradingMode::ModeB.allows_exits());
        assert!(TradingMode::ModeBPlus.allows_exits());
        assert!(!TradingMode::ModeC.allows_exits());
        assert!(!TradingMode::Dark.allows_exits());
    }

    #[test]
    fn test_trading_mode_market_data() {
        assert!(TradingMode::ModeA.requires_market_data());
        assert!(TradingMode::ModeB.requires_market_data());
        assert!(TradingMode::ModeBPlus.requires_market_data());
        assert!(!TradingMode::ModeC.requires_market_data());
        assert!(!TradingMode::Dark.requires_market_data());
    }
}
