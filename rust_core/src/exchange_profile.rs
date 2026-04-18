//! Exchange profiles — hours, timezones, auction windows for 9 exchanges.
//!
//! DST-aware via chrono-tz. All times are exchange-local.
//! V2 bug fix: exchange-aware EOD detection (not LSE times for all).

use chrono::NaiveTime;
use chrono_tz::Tz;

use crate::clock::parse_hhmm;

/// Profile for a single exchange.
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct ExchangeProfile {
    pub code: &'static str,
    pub name: &'static str,
    pub timezone: Tz,
    pub open_time: NaiveTime,
    pub close_time: NaiveTime,
    pub auction_open_start: Option<NaiveTime>,
    pub auction_close_start: Option<NaiveTime>,
    pub lunch_start: Option<NaiveTime>,
    pub lunch_end: Option<NaiveTime>,
}

impl ExchangeProfile {
    /// Lookup timezone for an exchange code. Falls back to UTC.
    pub fn timezone(exchange: &str) -> Tz {
        match exchange {
            "NYSE" | "NASDAQ" | "ARCA" | "SMART" | "BATS" => chrono_tz::America::New_York,
            "LSE" | "LSEETF" => chrono_tz::Europe::London,
            "IBIS" | "FWB" => chrono_tz::Europe::Berlin,
            "TSEJ" => chrono_tz::Asia::Tokyo,
            "SEHK" => chrono_tz::Asia::Hong_Kong,
            "KRX" => chrono_tz::Asia::Seoul,
            "SGX" => chrono_tz::Asia::Singapore,
            "SBF" | "ENEXT" => chrono_tz::Europe::Paris,
            "AQXE" => chrono_tz::Europe::London,
            _ => {
                tracing::warn!(exchange, "unknown exchange, defaulting to UTC");
                chrono_tz::UTC
            }
        }
    }

    /// Get the profile for a known exchange.
    pub fn get(exchange: &str) -> Self {
        match exchange {
            "NYSE" => Self::us_equities("NYSE"),
            "NASDAQ" => Self::us_equities("NASDAQ"),
            "ARCA" => Self::us_equities("ARCA"),
            "SMART" => Self::us_equities("SMART"),
            "BATS" => Self::us_equities("BATS"),
            "LSE" | "LSEETF" => Self::lse(),
            "IBIS" | "FWB" => Self::xetra(),
            "TSEJ" => Self::tokyo(),
            "SEHK" => Self::hong_kong(),
            "KRX" => Self::korea(),
            "SGX" => Self::singapore(),
            "SBF" | "ENEXT" => Self::euronext_paris(),
            "AQXE" => Self::aquis(),
            _ => {
                tracing::warn!(exchange, "unknown exchange, using UTC 09:30-16:00");
                Self {
                    code: "UNKNOWN",
                    name: "Unknown Exchange",
                    timezone: chrono_tz::UTC,
                    open_time: parse_hhmm("09:30"),
                    close_time: parse_hhmm("16:00"),
                    auction_open_start: None,
                    auction_close_start: None,
                    lunch_start: None,
                    lunch_end: None,
                }
            }
        }
    }

    // --- US ---

    fn us_equities(code: &'static str) -> Self {
        Self {
            code,
            name: "US Equities",
            timezone: chrono_tz::America::New_York,
            open_time: parse_hhmm("09:30"),
            close_time: parse_hhmm("16:00"),
            auction_open_start: Some(parse_hhmm("09:28")),
            auction_close_start: Some(parse_hhmm("15:50")),
            lunch_start: None,
            lunch_end: None,
        }
    }

    // --- LSE ---

    fn lse() -> Self {
        Self {
            code: "LSE",
            name: "London Stock Exchange",
            timezone: chrono_tz::Europe::London,
            open_time: parse_hhmm("08:00"),
            close_time: parse_hhmm("16:30"),
            auction_open_start: Some(parse_hhmm("07:50")),
            auction_close_start: Some(parse_hhmm("16:30")),
            lunch_start: None,
            lunch_end: None,
        }
    }

    // --- XETRA ---

    fn xetra() -> Self {
        Self {
            code: "IBIS",
            name: "XETRA",
            timezone: chrono_tz::Europe::Berlin,
            // XETRA extended hours (Dec 2025 update): 08:00-22:00 CET
            open_time: parse_hhmm("08:00"),
            close_time: parse_hhmm("22:00"),
            auction_open_start: Some(parse_hhmm("08:50")),
            auction_close_start: Some(parse_hhmm("17:30")),
            lunch_start: None,
            lunch_end: None,
        }
    }

    // --- Tokyo ---

    fn tokyo() -> Self {
        Self {
            code: "TSEJ",
            name: "Tokyo Stock Exchange",
            timezone: chrono_tz::Asia::Tokyo,
            open_time: parse_hhmm("09:00"),
            close_time: parse_hhmm("15:30"),
            auction_open_start: Some(parse_hhmm("08:00")),
            auction_close_start: Some(parse_hhmm("15:25")),
            lunch_start: Some(parse_hhmm("11:30")),
            lunch_end: Some(parse_hhmm("12:30")),
        }
    }

    // --- Hong Kong ---

    fn hong_kong() -> Self {
        Self {
            code: "SEHK",
            name: "Hong Kong Stock Exchange",
            timezone: chrono_tz::Asia::Hong_Kong,
            open_time: parse_hhmm("09:30"),
            close_time: parse_hhmm("16:00"),
            auction_open_start: Some(parse_hhmm("09:00")),
            auction_close_start: Some(parse_hhmm("16:00")),
            lunch_start: Some(parse_hhmm("12:00")),
            lunch_end: Some(parse_hhmm("13:00")),
        }
    }

    // --- Korea ---

    fn korea() -> Self {
        Self {
            code: "KRX",
            name: "Korea Exchange",
            timezone: chrono_tz::Asia::Seoul,
            open_time: parse_hhmm("09:00"),
            close_time: parse_hhmm("15:30"),
            auction_open_start: Some(parse_hhmm("08:30")),
            auction_close_start: Some(parse_hhmm("15:20")),
            lunch_start: None,
            lunch_end: None,
        }
    }

    // --- Singapore ---

    fn singapore() -> Self {
        Self {
            code: "SGX",
            name: "Singapore Exchange",
            timezone: chrono_tz::Asia::Singapore,
            open_time: parse_hhmm("09:00"),
            close_time: parse_hhmm("17:00"),
            auction_open_start: Some(parse_hhmm("08:30")),
            auction_close_start: Some(parse_hhmm("17:00")),
            lunch_start: None,
            lunch_end: None,
        }
    }

    // --- Euronext Paris ---

    fn euronext_paris() -> Self {
        Self {
            code: "SBF",
            name: "Euronext Paris",
            timezone: chrono_tz::Europe::Paris,
            open_time: parse_hhmm("09:00"),
            close_time: parse_hhmm("17:30"),
            auction_open_start: Some(parse_hhmm("07:15")),
            auction_close_start: Some(parse_hhmm("17:30")),
            lunch_start: None,
            lunch_end: None,
        }
    }

    // --- Aquis (AQXE) ---

    fn aquis() -> Self {
        Self {
            code: "AQXE",
            name: "Aquis Exchange",
            timezone: chrono_tz::Europe::London,
            open_time: parse_hhmm("08:00"),
            close_time: parse_hhmm("16:30"),
            auction_open_start: None,
            auction_close_start: None,
            lunch_start: None,
            lunch_end: None,
        }
    }

    /// Check if we're in the closing auction window (affects order types).
    #[allow(dead_code)]
    pub fn is_closing_auction(&self, local_time: NaiveTime) -> bool {
        if let Some(auction_start) = self.auction_close_start {
            local_time >= auction_start && local_time <= self.close_time
        } else {
            false
        }
    }

    /// Check if we're in the lunch break (Tokyo, Hong Kong).
    #[allow(dead_code)]
    pub fn is_lunch_break(&self, local_time: NaiveTime) -> bool {
        match (self.lunch_start, self.lunch_end) {
            (Some(start), Some(end)) => local_time >= start && local_time < end,
            _ => false,
        }
    }
}
