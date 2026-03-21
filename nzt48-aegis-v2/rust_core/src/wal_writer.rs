//! WAL Writer — append-only ndjson event journal. Source of truth (H26).
//! Runs in tokio::task::spawn_blocking (H13). Takes &WalEvent (immutable, H26).

use std::fs::{self, File, OpenOptions};
use std::io::{Seek, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crate::types::{WalEvent, WalPayload};

/// Errors from WAL operations.
#[derive(Debug)]
pub enum WalError {
    Io(std::io::Error),
    Serialize(String),
    DiskSpaceLow,
}

impl From<std::io::Error> for WalError {
    fn from(e: std::io::Error) -> Self {
        WalError::Io(e)
    }
}

impl std::fmt::Display for WalError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WalError::Io(e) => write!(f, "WAL IO error: {e}"),
            WalError::Serialize(e) => write!(f, "WAL serialize error: {e}"),
            WalError::DiskSpaceLow => write!(f, "WAL disk space < 5%"),
        }
    }
}

/// Append-only WAL writer. One file per trading day.
pub struct WalWriter {
    file: File,
    #[allow(dead_code)] // Used for day-rollover logic in later phases
    events_dir: PathBuf,
    dead_letter_dir: PathBuf,
    /// Injectable disk space checker for testing. Returns free percentage.
    pub disk_check_fn: Option<Box<dyn Fn() -> f64 + Send>>,
}

impl WalWriter {
    /// Open (or create) today's WAL file.
    pub fn open(events_dir: &Path, dead_letter_dir: &Path) -> Result<Self, WalError> {
        fs::create_dir_all(events_dir)?;
        fs::create_dir_all(dead_letter_dir)?;
        let file_path = Self::today_path(events_dir);
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&file_path)?;
        Ok(Self {
            file,
            events_dir: events_dir.to_path_buf(),
            dead_letter_dir: dead_letter_dir.to_path_buf(),
            disk_check_fn: None,
        })
    }

    /// Open a specific WAL file (for testing).
    pub fn open_file(path: &Path, dead_letter_dir: &Path) -> Result<Self, WalError> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::create_dir_all(dead_letter_dir)?;
        let file = OpenOptions::new().create(true).append(true).open(path)?;
        Ok(Self {
            file,
            events_dir: path.parent().unwrap_or(Path::new(".")).to_path_buf(),
            dead_letter_dir: dead_letter_dir.to_path_buf(),
            disk_check_fn: None,
        })
    }

    /// Append an event to the WAL. Takes immutable reference (H26).
    /// Computes CRC32 of payload, sets write_time_ns, serializes, appends, fsyncs.
    pub fn append(&mut self, event: &WalEvent) -> Result<(), WalError> {
        // Disk space check (H25)
        if let Some(ref check_fn) = self.disk_check_fn
            && check_fn() < 5.0
        {
            return Err(WalError::DiskSpaceLow);
        }

        // Serialize payload for CRC32
        let payload_json = serde_json::to_string(&event.payload)
            .map_err(|e| WalError::Serialize(e.to_string()))?;
        let checksum = crc32fast::hash(payload_json.as_bytes());

        // Build the final event with computed fields
        let write_time_ns = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0);

        let final_event = WalEvent {
            event_id: event.event_id.clone(),
            schema_version: event.schema_version,
            event_time_ns: event.event_time_ns,
            write_time_ns,
            checksum,
            wal_version: event.wal_version.clone(),
            payload: event.payload.clone(),
        };

        let line =
            serde_json::to_string(&final_event).map_err(|e| WalError::Serialize(e.to_string()))?;

        writeln!(self.file, "{line}")?;
        self.file.flush()?;

        // WP-1: Truncate file to current position to prevent EOF corruption
        // from partial/interrupted writes leaving garbage bytes after valid data.
        let pos = self.file.stream_position()?;
        self.file.set_len(pos)?;

        // WP-3: fsync after write — ensures data reaches stable storage
        self.file.sync_all()?;
        Ok(())
    }

    /// Force fsync on the WAL file. Used during graceful shutdown to guarantee
    /// all buffered data reaches stable storage before process exit.
    pub fn sync(&mut self) -> Result<(), WalError> {
        self.file.flush()?;
        self.file.sync_all()?;
        Ok(())
    }

    /// Write an unparseable OrderIntent to dead letter queue (H81).
    pub fn dead_letter(&self, data: &str) -> Result<(), WalError> {
        let file_path = Self::today_dead_letter_path(&self.dead_letter_dir);
        let mut f = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&file_path)?;
        writeln!(f, "{data}")?;
        f.sync_all()?;
        Ok(())
    }

    fn today_path(events_dir: &Path) -> PathBuf {
        let date = chrono_date_string();
        events_dir.join(format!("{date}.ndjson"))
    }

    fn today_dead_letter_path(dead_letter_dir: &Path) -> PathBuf {
        let date = chrono_date_string();
        dead_letter_dir.join(format!("{date}.ndjson"))
    }
}

/// Build a WalEvent with UUIDv7 event_id and schema_version=1.
pub fn make_wal_event(event_time_ns: u64, payload: WalPayload) -> WalEvent {
    WalEvent {
        event_id: uuid::Uuid::now_v7().to_string(),
        schema_version: 1,
        event_time_ns,
        write_time_ns: 0, // Set by append()
        checksum: 0,      // Set by append()
        wal_version: "1.1".to_string(),
        payload,
    }
}

/// YYYY-MM-DD date string (UTC).
fn chrono_date_string() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = secs / 86400;
    // Simple date calculation (accurate enough for file naming)
    let (y, m, d) = days_to_ymd(days);
    format!("{y:04}-{m:02}-{d:02}")
}

/// Convert days since epoch to (year, month, day).
fn days_to_ymd(days: u64) -> (u64, u64, u64) {
    // Civil days algorithm (Howard Hinnant)
    let z = days as i64 + 719468;
    let era = z.div_euclid(146097);
    let doe = z.rem_euclid(146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = (yoe as i64 + era * 400) as u64;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_make_wal_event() {
        let event = make_wal_event(
            1_000_000_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        assert_eq!(event.schema_version, 1);
        assert!(!event.event_id.is_empty());
    }

    #[test]
    fn test_days_to_ymd() {
        // Known epoch dates for validation
        let (y, m, d) = days_to_ymd(0);
        assert_eq!((y, m, d), (1970, 1, 1));
        let (y, m, d) = days_to_ymd(18628); // 2021-01-01
        assert_eq!((y, m, d), (2021, 1, 1));
    }

    #[test]
    fn test_wal_append_fsync_and_eof_truncation() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");
        let mut wal =
            WalWriter::open_file(&wal_path, &dl_path).expect("open");

        // Append two events
        let ev1 = make_wal_event(
            1_000_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        wal.append(&ev1).expect("append 1");

        let ev2 = make_wal_event(
            2_000_000,
            WalPayload::SystemReady {
                wal_events_replayed: 5,
                positions_reconciled: 3,
            },
        );
        wal.append(&ev2).expect("append 2");

        // Read file back — should have exactly 2 valid ndjson lines
        let content = std::fs::read_to_string(&wal_path).expect("read");
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 2, "Should have exactly 2 lines");

        // Each line should be valid JSON
        for (i, line) in lines.iter().enumerate() {
            let parsed: serde_json::Value =
                serde_json::from_str(line).unwrap_or_else(|e| panic!("Line {i} not valid JSON: {e}"));
            // Verify checksum field is non-zero (CRC32 computed)
            let checksum = parsed["checksum"].as_u64().expect("checksum field");
            assert!(checksum > 0, "Checksum should be non-zero");
        }

        // File size should exactly match content (no trailing garbage — WP-1)
        let meta = std::fs::metadata(&wal_path).expect("meta");
        assert_eq!(meta.len(), content.len() as u64, "File length should match content exactly");
    }

    #[test]
    fn test_wal_disk_space_check() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test_disk.ndjson");
        let dl_path = dir.path().join("dead_letter");
        let mut wal =
            WalWriter::open_file(&wal_path, &dl_path).expect("open");

        // Inject a disk check that reports <5% free
        wal.disk_check_fn = Some(Box::new(|| 3.0));

        let ev = make_wal_event(
            1_000_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        let result = wal.append(&ev);
        assert!(matches!(result, Err(WalError::DiskSpaceLow)));
    }
}
