//! IBKR-synced clock with exchange-aware timezone handling.
//!
//! - Syncs with IBKR server time via reqCurrentTime (server_time()).
//! - Uses chrono-tz for DST-safe exchange-local timestamps.
//! - Replaceable with a fixed clock for deterministic replay.

use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::Arc;

use chrono::{Datelike, DateTime, NaiveTime, Timelike, Utc};
use chrono_tz::Tz;
use tracing::{info, warn};

use crate::exchange_profile::ExchangeProfile;

/// Offset in microseconds: server_time - local_time.
/// Shared across threads via Arc<AtomicI64>.
static IBKR_OFFSET_US: AtomicI64 = AtomicI64::new(0);

// ---------------------------------------------------------------------------
// Clock trait — allows swapping real clock for replay
// ---------------------------------------------------------------------------

pub trait Clock: Send + Sync {
    /// Current UTC time in microseconds since epoch.
    fn now_us(&self) -> i64;

    /// Current UTC as chrono DateTime.
    fn now_utc(&self) -> DateTime<Utc> {
        let us = self.now_us();
        let secs = us / 1_000_000;
        let nanos = ((us % 1_000_000) * 1000) as u32;
        DateTime::from_timestamp(secs, nanos).unwrap_or_else(Utc::now)
    }

    /// Convert to exchange-local time.
    fn to_exchange_local(&self, exchange: &str) -> DateTime<Tz> {
        let tz = ExchangeProfile::timezone(exchange);
        self.now_utc().with_timezone(&tz)
    }

    /// Check if exchange is currently in trading hours.
    fn is_exchange_open(&self, profile: &ExchangeProfile) -> bool {
        let local = self.now_utc().with_timezone(&profile.timezone);
        let time = local.time();

        // Check weekday
        let weekday = local.weekday();
        if weekday == chrono::Weekday::Sat || weekday == chrono::Weekday::Sun {
            return false;
        }

        time >= profile.open_time && time < profile.close_time
    }

    /// Minutes until exchange close. Returns None if market is closed.
    fn minutes_to_close(&self, profile: &ExchangeProfile) -> Option<f64> {
        if !self.is_exchange_open(profile) {
            return None;
        }
        let local = self.now_utc().with_timezone(&profile.timezone);
        let time = local.time();
        let close = profile.close_time;

        let diff_secs = (close.num_seconds_from_midnight() as f64)
            - (time.num_seconds_from_midnight() as f64);
        if diff_secs > 0.0 {
            Some(diff_secs / 60.0)
        } else {
            None
        }
    }

    /// EOD tightening phase for Chandelier stops.
    /// Returns multiplier: 1.0 (normal), 0.80 (30-15min to close), 0.60 (≤15min to close).
    fn eod_tightening_mult(&self, profile: &ExchangeProfile) -> f64 {
        match self.minutes_to_close(profile) {
            Some(mins) if mins <= 15.0 => 0.60,
            Some(mins) if mins <= 30.0 => 0.80,
            _ => 1.0,
        }
    }
}

// ---------------------------------------------------------------------------
// LiveClock — production clock synced with IBKR
// ---------------------------------------------------------------------------

pub struct LiveClock;

impl LiveClock {
    /// Sync with IBKR server time. Call once after connection.
    /// Stores offset so all subsequent now_us() calls are server-relative.
    #[tracing::instrument(skip_all)]
    pub fn sync_with_ibkr(server_time_secs: i64) {
        let local_us = Utc::now().timestamp_micros();
        let server_us = server_time_secs * 1_000_000;
        let offset = server_us - local_us;
        IBKR_OFFSET_US.store(offset, Ordering::Relaxed);
        info!(
            offset_ms = offset / 1000,
            "clock synced with IBKR server (offset {:.1}ms)",
            offset as f64 / 1000.0
        );
    }
}

impl Clock for LiveClock {
    fn now_us(&self) -> i64 {
        let local_us = Utc::now().timestamp_micros();
        local_us + IBKR_OFFSET_US.load(Ordering::Relaxed)
    }
}

// ---------------------------------------------------------------------------
// ReplayClock — fixed/stepped clock for deterministic replay
// ---------------------------------------------------------------------------

#[allow(dead_code)]
pub struct ReplayClock {
    current_us: Arc<AtomicI64>,
}

#[allow(dead_code)]
impl ReplayClock {
    pub fn new(start_us: i64) -> Self {
        Self {
            current_us: Arc::new(AtomicI64::new(start_us)),
        }
    }

    pub fn set(&self, us: i64) {
        self.current_us.store(us, Ordering::Relaxed);
    }

    pub fn advance(&self, delta_us: i64) {
        self.current_us.fetch_add(delta_us, Ordering::Relaxed);
    }
}

impl Clock for ReplayClock {
    fn now_us(&self) -> i64 {
        self.current_us.load(Ordering::Relaxed)
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Parse a NaiveTime from "HH:MM" format.
pub fn parse_hhmm(s: &str) -> NaiveTime {
    NaiveTime::parse_from_str(s, "%H:%M").unwrap_or_else(|e| {
        warn!(input = s, error = %e, "failed to parse time, using midnight");
        NaiveTime::from_hms_opt(0, 0, 0).unwrap()
    })
}
