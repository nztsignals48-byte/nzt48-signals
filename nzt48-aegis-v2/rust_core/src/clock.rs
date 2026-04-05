//! Clock synchronisation, LSE market hours, UK holiday calendar.
//! CRITICAL: All times are UTC ONLY. No local timezone conversion.
//! Market hours are defined in UTC equivalents to avoid BST/GMT confusion.
//! LSE opens at 08:00 London time:
//! - GMT (Oct-Mar): 08:00 London = 08:00 UTC → LSE_OPEN_UTC_SECS = 8*3600
//! - BST (Mar-Oct): 08:00 London = 07:00 UTC → LSE_OPEN_UTC_SECS must account for this
//!   SOLUTION: Convert all market times to UTC ranges that account for DST dynamically.

/// LSE CONTINUOUS TRADING HOURS (UTC-aware):
/// During GMT (winter, Oct-Mar): 08:00-16:30 London = 08:00-16:30 UTC
/// During BST (summer, Mar-Oct): 08:00-16:30 London = 07:00-15:30 UTC
/// This function is called with current epoch time to determine correct range.
/// GST base (no DST): LSE 08:00 London
pub const LSE_OPEN_UTC_GMT: u32 = 8 * 3600; // 08:00 UTC (when no BST)
pub const LSE_OPEN_UTC_BST: u32 = 7 * 3600; // 07:00 UTC (when BST active)
pub const LSE_CLOSE_UTC_GMT: u32 = 16 * 3600 + 30 * 60; // 16:30 UTC (when no BST)
pub const LSE_CLOSE_UTC_BST: u32 = 15 * 3600 + 30 * 60; // 15:30 UTC (when BST active)
/// Entry cutoff (15:45 London):
/// - GMT: 15:45 UTC
/// - BST: 14:45 UTC
pub const ENTRY_CUTOFF_UTC_GMT: u32 = 15 * 3600 + 45 * 60;
pub const ENTRY_CUTOFF_UTC_BST: u32 = 14 * 3600 + 45 * 60;

/// Auction periods in London time, converted to UTC ranges:
/// Open auction: 07:50-08:00 London
pub const AUCTION_OPEN_START_UTC_GMT: u32 = 7 * 3600 + 50 * 60;
pub const AUCTION_OPEN_START_UTC_BST: u32 = 6 * 3600 + 50 * 60;
pub const AUCTION_OPEN_END_UTC_GMT: u32 = 8 * 3600;
pub const AUCTION_OPEN_END_UTC_BST: u32 = 7 * 3600;

/// Close auction: 16:30-16:35 London
pub const AUCTION_CLOSE_START_UTC_GMT: u32 = 16 * 3600 + 30 * 60;
pub const AUCTION_CLOSE_START_UTC_BST: u32 = 15 * 3600 + 30 * 60;
pub const AUCTION_CLOSE_END_UTC_GMT: u32 = 16 * 3600 + 35 * 60;
pub const AUCTION_CLOSE_END_UTC_BST: u32 = 15 * 3600 + 35 * 60;

/// EOD flatten phases in London time:
/// Phase 1 (T-35): 15:55 London
/// Phase 2 (T-15): 16:15 London
/// Phase 3 (T-5):  16:25 London
pub const EOD_PHASE1_UTC_GMT: u32 = 15 * 3600 + 55 * 60;
pub const EOD_PHASE1_UTC_BST: u32 = 14 * 3600 + 55 * 60;
pub const EOD_PHASE2_UTC_GMT: u32 = 16 * 3600 + 15 * 60;
pub const EOD_PHASE2_UTC_BST: u32 = 15 * 3600 + 15 * 60;
pub const EOD_PHASE3_UTC_GMT: u32 = 16 * 3600 + 25 * 60;
pub const EOD_PHASE3_UTC_BST: u32 = 15 * 3600 + 25 * 60;

/// Unified 2-mode trading clock (UTC-based).
/// Active = 22:00-20:00 UTC (22 hours across all 6 markets).
///   This corresponds to 23:00-21:00 London in winter (GMT)
///   and 23:00-21:00 UTC-1 in summer (during BST when London is UTC+1).
/// Dark = 20:00-22:00 UTC (maintenance window).
///
/// NOTE: We work in UTC throughout. The engine receives UTC epoch time from IBKR,
/// and converts to seconds-from-midnight-UTC for all mode checks.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TradingMode {
    /// Asia/pre-market session: 22:00-06:00 UTC.
    /// HKEX, TSE, SGX, ASX, KRX + pre-market scanning.
    Asia,
    /// European session: 06:00-12:30 UTC.
    /// LSE, XETRA, EURONEXT — peak leveraged ETP volume.
    Europe,
    /// US overlap session: 12:30-14:35 UTC.
    /// LSE + NYSE/NASDAQ open — highest cross-market liquidity.
    USOverlap,
    /// US-only session: 14:35-20:00 UTC.
    /// NYSE, NASDAQ power hours — post-LSE close.
    USSession,
    /// Dark: 20:00-22:00 UTC. Maintenance + Ouroboros nightly pipeline.
    Dark,
}

impl TradingMode {
    /// Determine the current trading mode from UTC seconds-from-midnight.
    /// This is the ONLY method that should be called. Input must be:
    /// `(current_utc_epoch_ns / 1_000_000_000) % 86400` = seconds from midnight UTC.
    ///
    /// Active sessions: 22:00-20:00 UTC (22 hours across 4 session windows).
    /// Dark: 20:00-22:00 UTC (2-hour maintenance window).
    pub fn from_utc_secs(utc_secs_from_midnight: u32) -> Self {
        const ACTIVE_START: u32 = 22 * 3600;  // 22:00 UTC
        const DARK_START: u32 = 20 * 3600;    // 20:00 UTC

        // Active hours: 22:00-23:59 and 00:00-20:00 (wraps midnight)
        if !(DARK_START..ACTIVE_START).contains(&utc_secs_from_midnight) {
            // Return session window for telemetry (all have identical permissions)
            match utc_secs_from_midnight {
                t if !(6 * 3600..ACTIVE_START).contains(&t) => TradingMode::Asia,        // 22:00-06:00 UTC (HKEX/TSE/SGX/ASX/KRX)
                t if t < 12 * 3600 + 30 * 60 => TradingMode::Europe,                // 06:00-12:30 UTC (LSE/XETRA/EURONEXT)
                t if t < 14 * 3600 + 35 * 60 => TradingMode::USOverlap,             // 12:30-14:35 UTC (LSE + NYSE/NASDAQ)
                _ => TradingMode::USSession,                                         // 14:35-20:00 UTC (NYSE/NASDAQ only)
            }
        } else {
            TradingMode::Dark
        }
    }

    /// Can new entries be submitted in this mode?
    /// All non-Dark modes allow entries (unified 22-hour trading).
    pub fn allows_entries(&self) -> bool {
        !matches!(self, TradingMode::Dark)
    }

    /// Can exits be executed in this mode?
    pub fn allows_exits(&self) -> bool {
        !matches!(self, TradingMode::Dark)
    }

    /// Should market data subscriptions be active?
    pub fn requires_market_data(&self) -> bool {
        !matches!(self, TradingMode::Dark)
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

    /// Is LSE in continuous trading right now (UTC)?
    /// Accounts for BST/GMT transitions automatically.
    /// Input: epoch_ns = current IBKR epoch in nanoseconds.
    pub fn is_lse_open_utc(epoch_ns: u64, is_bst: bool) -> bool {
        let utc_secs_from_midnight = ((epoch_ns / 1_000_000_000) % 86400) as u32;
        let (open, close) = if is_bst {
            (LSE_OPEN_UTC_BST, LSE_CLOSE_UTC_BST)
        } else {
            (LSE_OPEN_UTC_GMT, LSE_CLOSE_UTC_GMT)
        };
        (open..close).contains(&utc_secs_from_midnight)
    }

    /// Is the current time within an auction period (UTC)?
    pub fn is_auction_utc(utc_secs_from_midnight: u32, is_bst: bool) -> bool {
        let (open_start, open_end, close_start, close_end) = if is_bst {
            (AUCTION_OPEN_START_UTC_BST, AUCTION_OPEN_END_UTC_BST,
             AUCTION_CLOSE_START_UTC_BST, AUCTION_CLOSE_END_UTC_BST)
        } else {
            (AUCTION_OPEN_START_UTC_GMT, AUCTION_OPEN_END_UTC_GMT,
             AUCTION_CLOSE_START_UTC_GMT, AUCTION_CLOSE_END_UTC_GMT)
        };
        (open_start..open_end).contains(&utc_secs_from_midnight)
            || (close_start..close_end).contains(&utc_secs_from_midnight)
    }

    /// Is the current time past entry cutoff (15:45 London, UTC-adjusted)?
    pub fn is_after_cutoff_utc(utc_secs_from_midnight: u32, is_bst: bool) -> bool {
        let cutoff = if is_bst {
            ENTRY_CUTOFF_UTC_BST
        } else {
            ENTRY_CUTOFF_UTC_GMT
        };
        utc_secs_from_midnight >= cutoff
    }

    /// Which EOD flatten phase are we in (UTC)?
    pub fn eod_phase_utc(utc_secs_from_midnight: u32, is_bst: bool) -> Option<u8> {
        let (phase1, phase2, phase3) = if is_bst {
            (EOD_PHASE1_UTC_BST, EOD_PHASE2_UTC_BST, EOD_PHASE3_UTC_BST)
        } else {
            (EOD_PHASE1_UTC_GMT, EOD_PHASE2_UTC_GMT, EOD_PHASE3_UTC_GMT)
        };
        if utc_secs_from_midnight >= phase3 {
            Some(3) // T-5: MTL emergency
        } else if utc_secs_from_midnight >= phase2 {
            Some(2) // T-15: limit at mid
        } else if utc_secs_from_midnight >= phase1 {
            Some(1) // T-35: passive limit
        } else {
            None
        }
    }

    /// Is the given date (YYYY-MM-DD) a UK bank holiday?
    pub fn is_uk_holiday(&self, date: &str) -> bool {
        self.holidays.iter().any(|h| h == date)
    }

    /// Should we trade today (UTC-based)? Market is open AND not a holiday.
    pub fn is_trading_day(&self, date: &str, utc_secs: u32, is_bst: bool) -> bool {
        !self.is_uk_holiday(date) && Self::is_lse_open_utc(utc_secs as u64 * 1_000_000_000, is_bst)
    }

    /// Compute current UTC seconds-from-midnight.
    /// Applies broker clock offset (no timezone conversion).
    /// Input: system nanoseconds. Output: UTC seconds from midnight [0-86400).
    pub fn now_utc_secs(&self, system_ns: u64) -> u32 {
        // Apply broker clock offset to get "real" time
        let adjusted_ns = if self.offset_ns >= 0 {
            system_ns.wrapping_add(self.offset_ns as u64)
        } else {
            system_ns.wrapping_sub((-self.offset_ns) as u64)
        };
        let epoch_secs = adjusted_ns / 1_000_000_000;
        (epoch_secs % 86400) as u32
    }

    /// Get current UTC epoch seconds (for checking BST status, etc).
    pub fn now_utc_epoch(&self, system_ns: u64) -> u64 {
        let adjusted_ns = if self.offset_ns >= 0 {
            system_ns.wrapping_add(self.offset_ns as u64)
        } else {
            system_ns.wrapping_sub((-self.offset_ns) as u64)
        };
        adjusted_ns / 1_000_000_000
    }

    /// Check if a Unix epoch timestamp falls within BST (British Summer Time).
    /// BST starts: last Sunday of March at 01:00 UTC.
    /// BST ends: last Sunday of October at 01:00 UTC.
    /// Hardcoded for 2025-2032 (extend before 2033).
    pub fn is_bst_from_epoch(epoch_secs: u64) -> bool {
        // BST transition Unix timestamps (all at 01:00 UTC on the transition day).
        const YEAR_2025_START: u64 = 1_735_689_600;
        const YEAR_2033_START: u64 = 1_988_150_400;

        const BST_RANGES: [(u64, u64); 8] = [
            (1_743_296_400, 1_761_440_400), // 2025: Mar 30 01:00 UTC → Oct 26 01:00 UTC
            (1_774_746_000, 1_792_890_000), // 2026: Mar 29 01:00 UTC → Oct 25 01:00 UTC
            (1_806_195_600, 1_824_944_400), // 2027: Mar 28 01:00 UTC → Oct 31 01:00 UTC
            (1_837_645_200, 1_856_394_000), // 2028: Mar 26 01:00 UTC → Oct 29 01:00 UTC
            (1_869_094_800, 1_887_843_600), // 2029: Mar 25 01:00 UTC → Oct 28 01:00 UTC
            (1_901_149_200, 1_919_293_200), // 2030: Mar 31 01:00 UTC → Oct 27 01:00 UTC
            (1_932_598_800, 1_950_742_800), // 2031: Mar 30 01:00 UTC → Oct 26 01:00 UTC
            (1_964_048_400, 1_982_797_200), // 2032: Mar 28 01:00 UTC → Oct 31 01:00 UTC
        ];

        // For dates within 2025-2032, use exact transition timestamps
        if (YEAR_2025_START..YEAR_2033_START).contains(&epoch_secs) {
            for &(start, end) in &BST_RANGES {
                if epoch_secs >= start && epoch_secs < end {
                    return true;
                }
            }
            return false;
        }

        // Fallback for dates outside 2025-2032: use day-of-year approximation (±3 days)
        let day_of_year = ((epoch_secs / 86400) % 365) as u32;
        (84..301).contains(&day_of_year)
    }

    /// Fraction of LSE trading day elapsed [0.0, 1.0] (UTC-based).
    /// During GMT: 08:00 UTC = 0.0, 16:30 UTC = 1.0
    /// During BST: 07:00 UTC = 0.0, 15:30 UTC = 1.0
    pub fn time_of_day_fraction_utc(utc_secs_from_midnight: u32, is_bst: bool) -> f64 {
        let (open, close) = if is_bst {
            (LSE_OPEN_UTC_BST, LSE_CLOSE_UTC_BST)
        } else {
            (LSE_OPEN_UTC_GMT, LSE_CLOSE_UTC_GMT)
        };
        let trading_duration = close - open;
        if utc_secs_from_midnight <= open {
            return 0.0;
        }
        if utc_secs_from_midnight >= close {
            return 1.0;
        }
        (utc_secs_from_midnight - open) as f64 / trading_duration as f64
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_lse_open_hours_gmt() {
        // During GMT (winter): 08:00-16:30 London = 08:00-16:30 UTC
        assert!(!Clock::is_lse_open_utc(0, false)); // 00:00 UTC
        assert!(!Clock::is_lse_open_utc((7 * 3600) as u64 * 1_000_000_000, false)); // 07:00 UTC
        assert!(Clock::is_lse_open_utc((8 * 3600) as u64 * 1_000_000_000, false)); // 08:00 UTC
        assert!(Clock::is_lse_open_utc((12 * 3600) as u64 * 1_000_000_000, false)); // 12:00 UTC
        assert!(Clock::is_lse_open_utc((16 * 3600 + 29 * 60) as u64 * 1_000_000_000, false)); // 16:29 UTC
        assert!(!Clock::is_lse_open_utc((16 * 3600 + 30 * 60) as u64 * 1_000_000_000, false)); // 16:30 UTC
    }

    #[test]
    fn test_lse_open_hours_bst() {
        // During BST (summer): 08:00-16:30 London = 07:00-15:30 UTC
        assert!(!Clock::is_lse_open_utc((6 * 3600) as u64 * 1_000_000_000, true)); // 06:00 UTC
        assert!(Clock::is_lse_open_utc((7 * 3600) as u64 * 1_000_000_000, true)); // 07:00 UTC
        assert!(Clock::is_lse_open_utc((12 * 3600) as u64 * 1_000_000_000, true)); // 12:00 UTC
        assert!(Clock::is_lse_open_utc((15 * 3600 + 29 * 60) as u64 * 1_000_000_000, true)); // 15:29 UTC
        assert!(!Clock::is_lse_open_utc((15 * 3600 + 30 * 60) as u64 * 1_000_000_000, true)); // 15:30 UTC
    }

    #[test]
    fn test_auction_periods_gmt() {
        // Open: 07:50-08:00 London = 07:50-08:00 UTC (GMT)
        assert!(Clock::is_auction_utc(7 * 3600 + 50 * 60, false)); // 07:50 UTC
        assert!(Clock::is_auction_utc(7 * 3600 + 55 * 60, false)); // 07:55 UTC
        assert!(!Clock::is_auction_utc(8 * 3600, false)); // 08:00 UTC
        // Close: 16:30-16:35 London = 16:30-16:35 UTC (GMT)
        assert!(Clock::is_auction_utc(16 * 3600 + 30 * 60, false)); // 16:30 UTC
        assert!(Clock::is_auction_utc(16 * 3600 + 34 * 60, false)); // 16:34 UTC
        assert!(!Clock::is_auction_utc(16 * 3600 + 35 * 60, false)); // 16:35 UTC
    }

    #[test]
    fn test_auction_periods_bst() {
        // Open: 07:50-08:00 London = 06:50-07:00 UTC (BST)
        assert!(Clock::is_auction_utc(6 * 3600 + 50 * 60, true)); // 06:50 UTC
        assert!(Clock::is_auction_utc(6 * 3600 + 55 * 60, true)); // 06:55 UTC
        assert!(!Clock::is_auction_utc(7 * 3600, true)); // 07:00 UTC
        // Close: 16:30-16:35 London = 15:30-15:35 UTC (BST)
        assert!(Clock::is_auction_utc(15 * 3600 + 30 * 60, true)); // 15:30 UTC
        assert!(Clock::is_auction_utc(15 * 3600 + 34 * 60, true)); // 15:34 UTC
        assert!(!Clock::is_auction_utc(15 * 3600 + 35 * 60, true)); // 15:35 UTC
    }

    #[test]
    fn test_entry_cutoff_gmt() {
        // 15:45 London = 15:45 UTC (GMT)
        assert!(!Clock::is_after_cutoff_utc(15 * 3600 + 44 * 60, false)); // 15:44 UTC
        assert!(Clock::is_after_cutoff_utc(15 * 3600 + 45 * 60, false)); // 15:45 UTC
    }

    #[test]
    fn test_entry_cutoff_bst() {
        // 15:45 London = 14:45 UTC (BST)
        assert!(!Clock::is_after_cutoff_utc(14 * 3600 + 44 * 60, true)); // 14:44 UTC
        assert!(Clock::is_after_cutoff_utc(14 * 3600 + 45 * 60, true)); // 14:45 UTC
    }

    #[test]
    fn test_eod_phases_gmt() {
        assert_eq!(Clock::eod_phase_utc(15 * 3600, false), None);
        assert_eq!(Clock::eod_phase_utc(15 * 3600 + 55 * 60, false), Some(1));
        assert_eq!(Clock::eod_phase_utc(16 * 3600 + 15 * 60, false), Some(2));
        assert_eq!(Clock::eod_phase_utc(16 * 3600 + 25 * 60, false), Some(3));
    }

    #[test]
    fn test_eod_phases_bst() {
        assert_eq!(Clock::eod_phase_utc(14 * 3600, true), None);
        assert_eq!(Clock::eod_phase_utc(14 * 3600 + 55 * 60, true), Some(1));
        assert_eq!(Clock::eod_phase_utc(15 * 3600 + 15 * 60, true), Some(2));
        assert_eq!(Clock::eod_phase_utc(15 * 3600 + 25 * 60, true), Some(3));
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
        assert!(clock.is_trading_day("2026-03-09", 10 * 3600, false)); // Normal day (GMT)
        assert!(!clock.is_trading_day("2026-12-25", 10 * 3600, false)); // Holiday
        assert!(!clock.is_trading_day("2026-03-09", 7 * 3600, false)); // Before open
    }

    // ── BST transition tests ──
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
    fn test_utc_secs_extraction() {
        let clock = Clock::new(vec![]);
        // Test that now_utc_secs correctly extracts seconds from midnight UTC
        // Mar 30 2026 07:00 UTC = 1774746000 + 86400 + 6*3600 = 1774839600
        let utc_epoch_ns = 1_774_839_600u64 * 1_000_000_000;
        let utc_secs = clock.now_utc_secs(utc_epoch_ns);
        // Should be 07:00 UTC = 7*3600 = 25200 seconds from midnight
        assert_eq!(utc_secs, 7 * 3600, "07:00 UTC = 25200 seconds from midnight");
    }

    // ── Phase 11: TradingMode tests (UTC-based) ──
    #[test]
    fn test_trading_mode_transitions_utc() {
        // Unified: Active from 22:00-20:00 UTC, Dark from 20:00-22:00 UTC
        // Asia session: 22:00-06:00 UTC (HKEX/TSE/SGX/ASX/KRX + pre-market)
        assert_eq!(TradingMode::from_utc_secs(22 * 3600), TradingMode::Asia);
        assert_eq!(TradingMode::from_utc_secs(0), TradingMode::Asia);
        assert_eq!(TradingMode::from_utc_secs(3 * 3600), TradingMode::Asia);
        assert_eq!(TradingMode::from_utc_secs(5 * 3600 + 59 * 60), TradingMode::Asia);

        // Europe session: 06:00-12:30 UTC (LSE/XETRA/EURONEXT)
        assert_eq!(TradingMode::from_utc_secs(6 * 3600), TradingMode::Europe);
        assert_eq!(TradingMode::from_utc_secs(10 * 3600), TradingMode::Europe);
        assert_eq!(TradingMode::from_utc_secs(12 * 3600 + 29 * 60), TradingMode::Europe);

        // USOverlap session: 12:30-14:35 UTC (LSE + NYSE/NASDAQ)
        assert_eq!(TradingMode::from_utc_secs(12 * 3600 + 30 * 60), TradingMode::USOverlap);
        assert_eq!(TradingMode::from_utc_secs(14 * 3600 + 34 * 60), TradingMode::USOverlap);

        // USSession: 14:35-20:00 UTC (NYSE/NASDAQ only)
        assert_eq!(TradingMode::from_utc_secs(14 * 3600 + 35 * 60), TradingMode::USSession);
        assert_eq!(TradingMode::from_utc_secs(17 * 3600), TradingMode::USSession);
        assert_eq!(TradingMode::from_utc_secs(19 * 3600 + 59 * 60), TradingMode::USSession);

        // Dark: 20:00-22:00 UTC (maintenance window)
        assert_eq!(TradingMode::from_utc_secs(20 * 3600), TradingMode::Dark);
        assert_eq!(TradingMode::from_utc_secs(21 * 3600), TradingMode::Dark);
        assert_eq!(TradingMode::from_utc_secs(21 * 3600 + 59 * 60), TradingMode::Dark);
    }

    #[test]
    fn test_trading_mode_entry_rules() {
        // All non-Dark modes allow entries (unified 22-hour trading)
        assert!(TradingMode::Asia.allows_entries());
        assert!(TradingMode::Europe.allows_entries());
        assert!(TradingMode::USOverlap.allows_entries());
        assert!(TradingMode::USSession.allows_entries());
        assert!(!TradingMode::Dark.allows_entries());
    }

    #[test]
    fn test_trading_mode_exit_rules() {
        // All non-Dark modes allow exits
        assert!(TradingMode::Asia.allows_exits());
        assert!(TradingMode::Europe.allows_exits());
        assert!(TradingMode::USOverlap.allows_exits());
        assert!(TradingMode::USSession.allows_exits());
        assert!(!TradingMode::Dark.allows_exits());
    }

    #[test]
    fn test_trading_mode_market_data() {
        assert!(TradingMode::Asia.requires_market_data());
        assert!(TradingMode::Europe.requires_market_data());
        assert!(TradingMode::USOverlap.requires_market_data());
        assert!(TradingMode::USSession.requires_market_data());
        assert!(!TradingMode::Dark.requires_market_data());
    }
}
