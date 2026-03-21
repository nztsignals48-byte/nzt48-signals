//! Market Scheduler - Timezone-Adaptive Session Detection
//! 180 LOC enhancement: LSE + US + HK market hours, session routing, holiday calendar

use chrono::{DateTime, Datelike, NaiveDate, Timelike, Utc};
use chrono_tz::{America::New_York, Asia::Hong_Kong, Europe::London};

// ============================================================================
// Trading Session Enum
// ============================================================================

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TradingSession {
    Phase1Hk,            // 01:30-08:00 GMT (HK 09:30-16:00)
    Phase2Lse,           // 08:00-16:30 GMT (London main)
    Phase3Uspre,         // 09:00-14:30 GMT (US 04:00-09:30 ET pre-market)
    Phase4Uscash,        // 14:30-21:00 GMT (US 09:30-16:00 ET cash open)
    Phase5PowerHour,     // 20:00-21:00 GMT (US 15:00-16:00 ET power hour)
    Phase6AfterHours,    // 21:00-01:00 GMT (US 16:00-20:00 ET after-hours)
    Closed,              // Weekend or holiday
}

impl std::fmt::Display for TradingSession {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TradingSession::Phase1Hk => write!(f, "HK"),
            TradingSession::Phase2Lse => write!(f, "LSE"),
            TradingSession::Phase3Uspre => write!(f, "USPre"),
            TradingSession::Phase4Uscash => write!(f, "USCash"),
            TradingSession::Phase5PowerHour => write!(f, "PowerHour"),
            TradingSession::Phase6AfterHours => write!(f, "AfterHours"),
            TradingSession::Closed => write!(f, "Closed"),
        }
    }
}

// ============================================================================
// LSE Market Detection
// ============================================================================

pub struct LSEMarketHours {
    open_hour: u32,      // 08
    open_minute: u32,    // 00
    close_hour: u32,     // 16
    close_minute: u32,   // 30
}

impl LSEMarketHours {
    pub fn new() -> Self {
        Self {
            open_hour: 8,
            open_minute: 0,
            close_hour: 16,
            close_minute: 30,
        }
    }

    /// Check if LSE is open at given UTC time.
    /// FIX: Removed fake "lunch break" — LSE has NO mandatory lunch break.
    /// LSE trades continuously 08:00-16:30 London time.
    pub fn is_open(&self, utc: DateTime<Utc>) -> bool {
        let london_tz = London;
        let london_time = utc.with_timezone(&london_tz);

        // Weekday check (Mon=1, Fri=5, Sat=6, Sun=7)
        if london_time.weekday().number_from_monday() > 5 {
            return false;  // Weekend
        }

        let hour = london_time.hour();
        let minute = london_time.minute();

        // Before open
        if (hour < self.open_hour) || (hour == self.open_hour && minute < self.open_minute) {
            return false;
        }

        // After close
        if (hour > self.close_hour) || (hour == self.close_hour && minute > self.close_minute) {
            return false;
        }

        true
    }
}

impl Default for LSEMarketHours {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod lse_tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn test_lse_before_open() {
        let lse = LSEMarketHours::new();
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 7, 59, 0).expect("valid static date");  // Mon 07:59 GMT
        assert!(!lse.is_open(utc));
    }

    #[test]
    fn test_lse_at_open() {
        let lse = LSEMarketHours::new();
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 8, 0, 0).expect("valid static date");  // Mon 08:00 GMT
        assert!(lse.is_open(utc));
    }

    #[test]
    fn test_lse_midday_open() {
        // FIX: LSE has NO lunch break — trades continuously 08:00-16:30
        let lse = LSEMarketHours::new();
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 12, 1, 0).expect("valid static date");  // Mon 12:01 GMT
        assert!(lse.is_open(utc)); // Now correctly open
    }

    #[test]
    fn test_lse_at_close() {
        let lse = LSEMarketHours::new();
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 16, 30, 0).expect("valid static date");  // Mon 16:30 GMT
        assert!(lse.is_open(utc));
    }

    #[test]
    fn test_lse_weekend() {
        let lse = LSEMarketHours::new();
        let utc = Utc.with_ymd_and_hms(2026, 3, 14, 12, 0, 0).expect("valid static date");  // Sat
        assert!(!lse.is_open(utc));
    }
}

// ============================================================================
// US Market Detection
// ============================================================================

pub struct USMarketHours {
    premarket_open_h: u32,   // 04:00 ET
    premarket_open_m: u32,
    cash_open_h: u32,        // 09:30 ET
    cash_open_m: u32,
    cash_close_h: u32,       // 16:00 ET
    cash_close_m: u32,
    afterhours_close_h: u32, // 20:00 ET
    afterhours_close_m: u32,
}

impl USMarketHours {
    pub fn new() -> Self {
        Self {
            premarket_open_h: 4,  premarket_open_m: 0,
            cash_open_h: 9,       cash_open_m: 30,
            cash_close_h: 16,     cash_close_m: 0,
            afterhours_close_h: 20, afterhours_close_m: 0,
        }
    }

    fn hm(hour: u32, minute: u32) -> u32 { hour * 60 + minute }

    /// Check if US pre-market is open at given UTC time (04:00-09:30 ET)
    pub fn is_premarket_open(&self, utc: DateTime<Utc>) -> bool {
        let ny_tz = New_York;
        let ny_time = utc.with_timezone(&ny_tz);
        if ny_time.weekday().number_from_monday() > 5 { return false; }
        let hm = Self::hm(ny_time.hour(), ny_time.minute());
        hm >= Self::hm(self.premarket_open_h, self.premarket_open_m)
            && hm < Self::hm(self.cash_open_h, self.cash_open_m)
    }

    /// Check if US cash market is open (09:30-16:00 ET)
    pub fn is_cash_open(&self, utc: DateTime<Utc>) -> bool {
        let ny_tz = New_York;
        let ny_time = utc.with_timezone(&ny_tz);
        if ny_time.weekday().number_from_monday() > 5 { return false; }
        let hm = Self::hm(ny_time.hour(), ny_time.minute());
        hm >= Self::hm(self.cash_open_h, self.cash_open_m)
            && hm < Self::hm(self.cash_close_h, self.cash_close_m)
    }

    /// Check if US after-hours is open (16:00-20:00 ET)
    pub fn is_afterhours_open(&self, utc: DateTime<Utc>) -> bool {
        let ny_tz = New_York;
        let ny_time = utc.with_timezone(&ny_tz);
        if ny_time.weekday().number_from_monday() > 5 { return false; }
        let hm = Self::hm(ny_time.hour(), ny_time.minute());
        hm >= Self::hm(self.cash_close_h, self.cash_close_m)
            && hm < Self::hm(self.afterhours_close_h, self.afterhours_close_m)
    }
}

impl Default for USMarketHours {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod us_tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn test_us_premarket_open() {
        let us = USMarketHours::new();
        // 09:00 GMT = 04:00 ET (Mon)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 9, 0, 0).expect("valid static date");
        assert!(us.is_premarket_open(utc));
    }

    #[test]
    fn test_us_cash_open() {
        let us = USMarketHours::new();
        // 14:30 GMT = 09:30 ET (Mon)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 14, 30, 0).expect("valid static date");
        assert!(us.is_cash_open(utc));
    }

    #[test]
    fn test_us_afterhours_open() {
        let us = USMarketHours::new();
        // 21:00 GMT = 16:00 ET (Mon)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 21, 0, 0).expect("valid static date");
        assert!(us.is_afterhours_open(utc));
    }
}

// ============================================================================
// HK Market Detection
// ============================================================================

pub struct HKMarketHours {
    open_h: u32,          // 09:30 HKT = 01:30 GMT
    open_m: u32,
    lunch_start_h: u32,   // 12:00 HKT = 04:00 GMT
    lunch_start_m: u32,
    lunch_end_h: u32,     // 13:00 HKT = 05:00 GMT
    lunch_end_m: u32,
    close_h: u32,         // 16:00 HKT = 08:00 GMT
    close_m: u32,
}

impl HKMarketHours {
    pub fn new() -> Self {
        Self {
            open_h: 9,    open_m: 30,
            lunch_start_h: 12, lunch_start_m: 0,
            lunch_end_h: 13,   lunch_end_m: 0,
            close_h: 16,       close_m: 0,
        }
    }

    fn hm(hour: u32, minute: u32) -> u32 { hour * 60 + minute }

    /// Check if HK market is open at given UTC time (09:30-12:00, 13:00-16:00 HKT)
    pub fn is_open(&self, utc: DateTime<Utc>) -> bool {
        let hk_tz = Hong_Kong;
        let hk_time = utc.with_timezone(&hk_tz);

        // Weekday check
        if hk_time.weekday().number_from_monday() > 5 {
            return false;
        }

        let hm = Self::hm(hk_time.hour(), hk_time.minute());
        let open = Self::hm(self.open_h, self.open_m);
        let lunch_start = Self::hm(self.lunch_start_h, self.lunch_start_m);
        let lunch_end = Self::hm(self.lunch_end_h, self.lunch_end_m);
        let close = Self::hm(self.close_h, self.close_m);

        // Morning session: 09:30-12:00
        if hm >= open && hm < lunch_start {
            return true;
        }
        // Afternoon session: 13:00-16:00
        if hm >= lunch_end && hm < close {
            return true;
        }

        false
    }
}

impl Default for HKMarketHours {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod hk_tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn test_hk_open() {
        let hk = HKMarketHours::new();
        // 01:30 GMT = 09:30 HKT (Mon)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 1, 30, 0).expect("valid static date");
        assert!(hk.is_open(utc));
    }

    #[test]
    fn test_hk_lunch_break() {
        let hk = HKMarketHours::new();
        // 04:30 GMT = 12:30 HKT (Mon)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 4, 30, 0).expect("valid static date");
        assert!(!hk.is_open(utc));
    }
}

// ============================================================================
// Session Router
// ============================================================================

pub fn get_current_session(utc: DateTime<Utc>) -> TradingSession {
    let lse = LSEMarketHours::new();
    let us = USMarketHours::new();
    let hk = HKMarketHours::new();

    // Check holidays per exchange (don't return Closed globally — one may be open while another isn't).
    let today_london = utc.with_timezone(&London).date_naive();
    let today_ny = utc.with_timezone(&New_York).date_naive();

    let lse_holiday = HolidayCalendar::is_uk_holiday(today_london);
    let us_holiday = HolidayCalendar::is_us_holiday(today_ny);

    // Priority: LSE > US > HK (LSE is our primary market for ISA ETPs)
    // Check LSE first since it's our primary market
    if !lse_holiday && lse.is_open(utc) {
        return TradingSession::Phase2Lse;
    }

    // HK session (only when LSE is closed — no overlap needed)
    if hk.is_open(utc) {
        return TradingSession::Phase1Hk;
    }

    // US sessions (pre-market, cash, power hour, after-hours)
    if !us_holiday {
        if us.is_premarket_open(utc) {
            return TradingSession::Phase3Uspre;
        }
        if us.is_cash_open(utc) {
            let ny_tz = New_York;
            let ny_time = utc.with_timezone(&ny_tz);
            return if ny_time.hour() == 15 {
                TradingSession::Phase5PowerHour
            } else {
                TradingSession::Phase4Uscash
            };
        }
        if us.is_afterhours_open(utc) {
            return TradingSession::Phase6AfterHours;
        }
    }

    TradingSession::Closed
}

#[cfg(test)]
mod session_router_tests {
    use super::*;
    use chrono::TimeZone;

    #[test]
    fn test_hk_session_detection() {
        // 01:30 GMT = 09:30 HKT (Mon)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 1, 30, 0).expect("valid static date");
        assert_eq!(get_current_session(utc), TradingSession::Phase1Hk);
    }

    #[test]
    fn test_lse_session_detection() {
        // 10:00 GMT (Mon)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 10, 0, 0).expect("valid static date");
        assert_eq!(get_current_session(utc), TradingSession::Phase2Lse);
    }

    #[test]
    fn test_us_cash_session_detection() {
        // 17:00 GMT = 12:00 EDT (Mon 16 Mar 2026, after DST: UTC-4, during US 09:30-16:00)
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 17, 0, 0).expect("valid static date");
        assert_eq!(get_current_session(utc), TradingSession::Phase4Uscash);
    }

    #[test]
    fn test_power_hour_detection() {
        // 19:00 GMT = 15:00 EDT (Mon 16 Mar 2026, after DST: UTC-4)
        // Power hour is the last hour of cash market: 15:00-16:00 ET = 19:00-20:00 GMT
        let utc = Utc.with_ymd_and_hms(2026, 3, 16, 19, 0, 0).expect("valid static date");
        assert_eq!(get_current_session(utc), TradingSession::Phase5PowerHour);
    }

    #[test]
    fn test_closed_session_weekend() {
        // Sat 12:00 GMT
        let utc = Utc.with_ymd_and_hms(2026, 3, 14, 12, 0, 0).expect("valid static date");
        assert_eq!(get_current_session(utc), TradingSession::Closed);
    }
}

// ============================================================================
// Holiday Calendar
// ============================================================================

pub struct HolidayCalendar;

impl HolidayCalendar {
    /// Check if date is a UK bank holiday (2026-2027).
    pub fn is_uk_holiday(date: NaiveDate) -> bool {
        let y = date.year();
        let m = date.month();
        let d = date.day();
        match (y, m, d) {
            // 2026 UK bank holidays
            (2026, 1, 1) => true,   // New Year's Day
            (2026, 4, 3) => true,   // Good Friday
            (2026, 4, 6) => true,   // Easter Monday
            (2026, 5, 4) => true,   // Early May Bank Holiday
            (2026, 5, 25) => true,  // Spring Bank Holiday
            (2026, 8, 31) => true,  // Summer Bank Holiday
            (2026, 12, 25) => true, // Christmas Day
            (2026, 12, 28) => true, // Boxing Day (observed, 26 Dec = Sat)
            // 2027 UK bank holidays
            (2027, 1, 1) => true,
            (2027, 3, 26) => true,  // Good Friday
            (2027, 3, 29) => true,  // Easter Monday
            (2027, 5, 3) => true,
            (2027, 5, 31) => true,
            (2027, 8, 30) => true,
            (2027, 12, 27) => true, // Christmas observed
            (2027, 12, 28) => true, // Boxing Day observed
            _ => false,
        }
    }

    /// Check if date is a US market holiday (NYSE/NASDAQ closed, 2026-2027).
    pub fn is_us_holiday(date: NaiveDate) -> bool {
        let y = date.year();
        let m = date.month();
        let d = date.day();
        match (y, m, d) {
            // 2026 US market holidays
            (2026, 1, 1) => true,   // New Year's Day
            (2026, 1, 19) => true,  // MLK Day
            (2026, 2, 16) => true,  // Presidents Day
            (2026, 4, 3) => true,   // Good Friday
            (2026, 5, 25) => true,  // Memorial Day
            (2026, 6, 19) => true,  // Juneteenth
            (2026, 7, 3) => true,   // Independence Day observed (Jul 4 = Sat)
            (2026, 9, 7) => true,   // Labor Day
            (2026, 11, 26) => true, // Thanksgiving
            (2026, 12, 25) => true, // Christmas
            // 2027 US market holidays
            (2027, 1, 1) => true,
            (2027, 1, 18) => true,
            (2027, 2, 15) => true,
            (2027, 3, 26) => true,  // Good Friday
            (2027, 5, 31) => true,
            (2027, 6, 18) => true,  // Juneteenth observed (19 = Sat)
            (2027, 7, 5) => true,   // Independence Day observed (Jul 4 = Sun)
            (2027, 9, 6) => true,
            (2027, 11, 25) => true,
            (2027, 12, 24) => true, // Christmas observed (25 = Sat)
            _ => false,
        }
    }

    /// Generic holiday check (any major market).
    pub fn is_holiday(date: NaiveDate) -> bool {
        Self::is_uk_holiday(date) || Self::is_us_holiday(date)
    }

    /// Check if a trading day should be marked as closed (UK-centric).
    pub fn is_market_closed(date: NaiveDate) -> bool {
        let weekday = date.weekday().number_from_monday();
        if weekday > 5 {
            return true;
        }
        Self::is_uk_holiday(date)
    }

    /// Check if US markets are closed on this date.
    pub fn is_us_market_closed(date: NaiveDate) -> bool {
        let weekday = date.weekday().number_from_monday();
        if weekday > 5 {
            return true;
        }
        Self::is_us_holiday(date)
    }
}

#[cfg(test)]
mod holiday_tests {
    use super::*;

    #[test]
    fn test_new_years_day() {
        let date = NaiveDate::from_ymd_opt(2026, 1, 1).expect("valid static date");
        assert!(HolidayCalendar::is_uk_holiday(date));
        assert!(HolidayCalendar::is_us_holiday(date));
    }

    #[test]
    fn test_good_friday_2026() {
        let date = NaiveDate::from_ymd_opt(2026, 4, 3).expect("valid static date");
        assert!(HolidayCalendar::is_uk_holiday(date));
        assert!(HolidayCalendar::is_us_holiday(date));
    }

    #[test]
    fn test_christmas() {
        let date = NaiveDate::from_ymd_opt(2026, 12, 25).expect("valid static date");
        assert!(HolidayCalendar::is_uk_holiday(date));
    }

    #[test]
    fn test_us_only_holiday() {
        // MLK Day is US-only, not UK
        let date = NaiveDate::from_ymd_opt(2026, 1, 19).expect("valid static date");
        assert!(HolidayCalendar::is_us_holiday(date));
        assert!(!HolidayCalendar::is_uk_holiday(date));
    }

    #[test]
    fn test_regular_trading_day() {
        let date = NaiveDate::from_ymd_opt(2026, 3, 16).expect("valid static date");
        assert!(!HolidayCalendar::is_holiday(date));
        assert!(!HolidayCalendar::is_market_closed(date));
    }

    #[test]
    fn test_saturday_closed() {
        let date = NaiveDate::from_ymd_opt(2026, 3, 14).expect("valid static date");
        assert!(HolidayCalendar::is_market_closed(date));
    }
}
