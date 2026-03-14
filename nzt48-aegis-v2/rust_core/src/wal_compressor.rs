//! WAL Compression & Archival — Phase 18.
//! Tracks WAL event counts and decides when to rotate/archive/purge.
//! Actual file I/O (gzip, rename) happens in the engine main loop;
//! this module only manages state and rotation/purge decisions.

/// Tracks WAL event volume and decides when rotation is needed.
pub struct WalCompressor {
    pub max_events_per_file: u64,
    pub archive_dir: String,
    pub events_written: u64,
    pub current_wal_path: String,
}

impl WalCompressor {
    /// Create a new compressor tracking the given WAL file.
    pub fn new(wal_path: &str, archive_dir: &str, max_events: u64) -> Self {
        Self {
            max_events_per_file: max_events,
            archive_dir: archive_dir.to_string(),
            events_written: 0,
            current_wal_path: wal_path.to_string(),
        }
    }

    /// Record that one event was written to the current WAL.
    pub fn record_event(&mut self) {
        self.events_written += 1;
    }

    /// True if the current WAL has reached its rotation threshold.
    pub fn needs_rotation(&self) -> bool {
        self.events_written >= self.max_events_per_file
    }

    /// Generate the archive target path using the current UTC time.
    /// Format: `{archive_dir}/wal_YYYYMMDD_HHMMSS.ndjson.gz`
    pub fn rotation_target_path(&self) -> String {
        let now = now_utc();
        format!(
            "{}/wal_{}_{}.ndjson.gz",
            self.archive_dir,
            format_date(now),
            format_time(now),
        )
    }

    /// Reset the event counter (call after successful rotation).
    pub fn reset_counter(&mut self) {
        self.events_written = 0;
    }

    /// Parse the date from an archive filename and return the number of days
    /// since that date. Returns `None` if the filename doesn't match the
    /// expected `wal_YYYYMMDD_HHMMSS.ndjson.gz` pattern.
    pub fn archive_age_days(archive_path: &str) -> Option<u64> {
        let filename = archive_path.rsplit('/').next().unwrap_or(archive_path);
        // Expected: wal_YYYYMMDD_HHMMSS.ndjson.gz
        let stem = filename.strip_prefix("wal_")?;
        if stem.len() < 15 {
            return None;
        }
        let date_part = &stem[..8]; // YYYYMMDD
        let year: i64 = date_part.get(..4)?.parse().ok()?;
        let month: i64 = date_part.get(4..6)?.parse().ok()?;
        let day: i64 = date_part.get(6..8)?.parse().ok()?;

        let archive_epoch_days = days_from_civil(year, month, day);
        let today_epoch_days = now_utc() / 86400;
        if today_epoch_days >= archive_epoch_days {
            Some((today_epoch_days - archive_epoch_days) as u64)
        } else {
            Some(0)
        }
    }

    /// True if the archive file is older than `max_age_days`.
    /// Default retention is 90 days (3 months).
    pub fn should_purge(archive_path: &str, max_age_days: u64) -> bool {
        match Self::archive_age_days(archive_path) {
            Some(age) => age > max_age_days,
            None => false, // Can't parse → don't purge
        }
    }
}

/// Monthly rotation policy for bulk purge decisions.
pub struct MonthlyRotation {
    pub retention_months: u32,
}

impl MonthlyRotation {
    /// Create with the given retention period (default 3 months = ~90 days).
    pub fn new(retention_months: u32) -> Self {
        Self { retention_months }
    }

    /// Return archive paths that exceed the retention period.
    pub fn archives_to_purge(&self, archive_paths: &[String]) -> Vec<String> {
        let max_age_days = u64::from(self.retention_months) * 30;
        archive_paths
            .iter()
            .filter(|p| WalCompressor::should_purge(p, max_age_days))
            .cloned()
            .collect()
    }
}

impl Default for MonthlyRotation {
    fn default() -> Self {
        Self::new(3)
    }
}

// ── internal helpers (no external deps) ──────────────────────────────

/// Current UTC timestamp as seconds since epoch.
fn now_utc() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

/// Format epoch seconds → "YYYYMMDD".
fn format_date(epoch_secs: i64) -> String {
    let (y, m, d) = civil_from_days(epoch_secs / 86400);
    format!("{y:04}{m:02}{d:02}")
}

/// Format epoch seconds → "HHMMSS".
fn format_time(epoch_secs: i64) -> String {
    let day_secs = epoch_secs.rem_euclid(86400);
    let h = day_secs / 3600;
    let m = (day_secs % 3600) / 60;
    let s = day_secs % 60;
    format!("{h:02}{m:02}{s:02}")
}

/// Convert days since Unix epoch to (year, month, day).
/// Algorithm from Howard Hinnant (public domain).
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// Convert (year, month, day) to days since Unix epoch.
/// Inverse of `civil_from_days` (Howard Hinnant).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rotation_trigger_at_threshold() {
        let mut c = WalCompressor::new("events/wal.ndjson", "events/archive", 100);
        for _ in 0..99 {
            c.record_event();
        }
        assert!(!c.needs_rotation());
        c.record_event(); // 100th
        assert!(c.needs_rotation());
    }

    #[test]
    fn test_rotation_trigger_above_threshold() {
        let mut c = WalCompressor::new("events/wal.ndjson", "events/archive", 50);
        for _ in 0..60 {
            c.record_event();
        }
        assert!(c.needs_rotation());
    }

    #[test]
    fn test_path_generation_format() {
        let c = WalCompressor::new("events/wal.ndjson", "events/archive", 1_000_000);
        let path = c.rotation_target_path();
        assert!(path.starts_with("events/archive/wal_"));
        assert!(path.ends_with(".ndjson.gz"));
        // Should be: events/archive/wal_YYYYMMDD_HHMMSS.ndjson.gz
        let filename = path.rsplit('/').next().expect("has filename");
        assert_eq!(filename.len(), "wal_20260313_235000.ndjson.gz".len());
    }

    #[test]
    fn test_counter_reset() {
        let mut c = WalCompressor::new("events/wal.ndjson", "events/archive", 10);
        for _ in 0..15 {
            c.record_event();
        }
        assert_eq!(c.events_written, 15);
        c.reset_counter();
        assert_eq!(c.events_written, 0);
        assert!(!c.needs_rotation());
    }

    #[test]
    fn test_archive_age_days_known_date() {
        // Use a date far in the past so we know age > 0
        let age = WalCompressor::archive_age_days("events/archive/wal_20200101_120000.ndjson.gz");
        assert!(age.is_some());
        let days = age.expect("parsed");
        // 2020-01-01 is at least 1800 days ago from 2025+
        assert!(days > 1800, "expected >1800, got {days}");
    }

    #[test]
    fn test_archive_age_days_invalid() {
        assert!(WalCompressor::archive_age_days("random_file.txt").is_none());
        assert!(WalCompressor::archive_age_days("wal_bad.ndjson.gz").is_none());
    }

    #[test]
    fn test_should_purge_old_archive() {
        // 2020-01-01 is well over 90 days ago
        assert!(WalCompressor::should_purge(
            "events/archive/wal_20200101_000000.ndjson.gz",
            90
        ));
    }

    #[test]
    fn test_should_not_purge_recent() {
        // Use today's date — should not purge
        let today = format_date(now_utc());
        let time = format_time(now_utc());
        let path = format!("events/archive/wal_{today}_{time}.ndjson.gz");
        assert!(!WalCompressor::should_purge(&path, 90));
    }

    #[test]
    fn test_monthly_rotation_purge_list() {
        let rot = MonthlyRotation::new(3); // 90 days
        let paths = vec![
            "events/archive/wal_20200101_000000.ndjson.gz".to_string(), // old
            "events/archive/wal_20200601_120000.ndjson.gz".to_string(), // old
            format!(
                "events/archive/wal_{}_{}.ndjson.gz",
                format_date(now_utc()),
                format_time(now_utc()),
            ), // today
        ];
        let purged = rot.archives_to_purge(&paths);
        assert_eq!(purged.len(), 2);
        assert!(purged.contains(&paths[0]));
        assert!(purged.contains(&paths[1]));
    }

    #[test]
    fn test_civil_roundtrip() {
        // Verify Hinnant algorithm round-trips for a known date
        let days = days_from_civil(2026, 3, 13);
        let (y, m, d) = civil_from_days(days);
        assert_eq!((y, m, d), (2026, 3, 13));
    }
}
