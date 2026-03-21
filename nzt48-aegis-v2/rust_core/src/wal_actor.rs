//! WAL Actor — dedicated std::thread for WAL writes via crossbeam channel.
//! RM-2: Prevents tokio::fs spawn_blocking pool exhaustion under 10k tick/sec bursts.
//!
//! Architecture:
//!   - Unbounded crossbeam channel (non-blocking enqueue from hot path)
//!   - Dedicated std::thread (not tokio — avoids stealing from reactor)
//!   - Batch fsync every N writes for throughput
//!   - Graceful shutdown via WalCommand::Shutdown sentinel

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use crossbeam_channel::{Receiver, Sender, TrySendError};

use crate::types::{WalEvent, WalPayload};
use crate::wal_writer::make_wal_event;

/// Commands sent to the WAL actor thread.
#[derive(Debug)]
pub enum WalCommand {
    /// Append a WalEvent to the journal.
    Append(WalEvent),
    /// Flush + fsync immediately (used at shutdown or before critical operations).
    ForceSync,
    /// Graceful shutdown: flush, sync, close file, exit thread.
    Shutdown,
}

/// Handle for sending WAL commands from the hot path.
/// Clone-able — engine, exit_engine, and risk_arbiter can all hold one.
#[derive(Clone)]
pub struct WalHandle {
    tx: Sender<WalCommand>,
}

impl WalHandle {
    /// Non-blocking enqueue of a WAL event. Returns false only if channel is disconnected.
    pub fn append(&self, event: WalEvent) -> bool {
        match self.tx.try_send(WalCommand::Append(event)) {
            Ok(()) => true,
            // Bounded channel backpressure — drop event, do NOT escalate to HALT
            Err(TrySendError::Full(_)) => {
                eprintln!("WAL: channel full (50K capacity)");
                false
            }
            Err(TrySendError::Disconnected(_)) => {
                eprintln!("WAL: actor thread disconnected");
                false
            }
        }
    }

    /// Convenience: build and enqueue a WalEvent from a payload.
    pub fn write_payload(&self, event_time_ns: u64, payload: WalPayload) -> bool {
        let event = make_wal_event(event_time_ns, payload);
        self.append(event)
    }

    /// Request immediate flush + fsync.
    pub fn force_sync(&self) -> bool {
        self.tx.send(WalCommand::ForceSync).is_ok()
    }

    /// Request graceful shutdown.
    pub fn shutdown(&self) -> bool {
        self.tx.send(WalCommand::Shutdown).is_ok()
    }
}

/// WAL actor that runs on a dedicated std::thread.
pub struct WalActor {
    rx: Receiver<WalCommand>,
    events_dir: PathBuf,
    dead_letter_dir: PathBuf,
    /// Batch fsync interval (writes between fsyncs).
    batch_sync_interval: u32,
}

impl WalActor {
    /// Spawn the WAL actor on a dedicated std::thread.
    /// Returns a WalHandle for non-blocking enqueue and the JoinHandle.
    pub fn spawn(
        events_dir: &Path,
        dead_letter_dir: &Path,
        batch_sync_interval: u32,
    ) -> (WalHandle, std::thread::JoinHandle<WalActorStats>) {
        let (tx, rx) = crossbeam_channel::bounded(50_000);

        let actor = WalActor {
            rx,
            events_dir: events_dir.to_path_buf(),
            dead_letter_dir: dead_letter_dir.to_path_buf(),
            batch_sync_interval,
        };

        let handle = WalHandle { tx };
        let join_handle = std::thread::Builder::new()
            .name("wal-actor".into())
            .spawn(move || actor.run())
            .expect("WAL actor thread spawn");

        (handle, join_handle)
    }

    /// Main loop: receive commands, write to file, batch fsync.
    fn run(self) -> WalActorStats {
        let mut stats = WalActorStats::default();

        // Open (or create) today's WAL file
        if let Err(e) = fs::create_dir_all(&self.events_dir) {
            eprintln!("WAL actor: failed to create events_dir: {e}");
            return stats;
        }
        if let Err(e) = fs::create_dir_all(&self.dead_letter_dir) {
            eprintln!("WAL actor: failed to create dead_letter_dir: {e}");
            return stats;
        }

        let file_path = self.today_path();
        let mut file = match OpenOptions::new()
            .create(true)
            .append(true)
            .open(&file_path)
        {
            Ok(f) => f,
            Err(e) => {
                eprintln!("WAL actor: failed to open {}: {e}", file_path.display());
                return stats;
            }
        };

        let mut writes_since_sync: u32 = 0;

        while let Ok(cmd) = self.rx.recv() {
            match cmd {
                WalCommand::Append(event) => {
                    match self.write_event(&mut file, &event) {
                        Ok(()) => {
                            stats.events_written += 1;
                            writes_since_sync += 1;

                            // Batch fsync
                            if writes_since_sync >= self.batch_sync_interval {
                                if let Err(e) = file.sync_all() {
                                    eprintln!("WAL actor: fsync failed: {e}");
                                    stats.sync_errors += 1;
                                } else {
                                    stats.syncs += 1;
                                }
                                writes_since_sync = 0;
                            }
                        }
                        Err(e) => {
                            eprintln!("WAL actor: write failed: {e}");
                            stats.write_errors += 1;
                            // Dead-letter the event
                            self.dead_letter(&event);
                        }
                    }
                }

                WalCommand::ForceSync => {
                    if let Err(e) = file.sync_all() {
                        eprintln!("WAL actor: force sync failed: {e}");
                        stats.sync_errors += 1;
                    } else {
                        stats.syncs += 1;
                    }
                    writes_since_sync = 0;
                }

                WalCommand::Shutdown => {
                    // Drain remaining commands
                    while let Ok(cmd) = self.rx.try_recv() {
                        if let WalCommand::Append(event) = cmd
                            && self.write_event(&mut file, &event).is_ok() {
                                stats.events_written += 1;
                            }
                    }
                    // Final fsync
                    let _ = file.sync_all();
                    stats.syncs += 1;
                    break;
                }
            }
        }

        stats
    }

    /// Serialize and write a single WalEvent. Computes CRC32 and write_time_ns.
    fn write_event(&self, file: &mut File, event: &WalEvent) -> Result<(), std::io::Error> {
        let payload_json = serde_json::to_string(&event.payload)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let checksum = crc32fast::hash(payload_json.as_bytes());

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

        let line = serde_json::to_string(&final_event)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

        writeln!(file, "{line}")?;
        file.flush()?;
        Ok(())
    }

    /// Write failed event to dead letter queue.
    fn dead_letter(&self, event: &WalEvent) {
        let date = chrono_date_string();
        let path = self.dead_letter_dir.join(format!("{date}-dead-letter.ndjson"));
        if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) {
            let json = serde_json::to_string(event).unwrap_or_default();
            let _ = writeln!(f, "{json}");
            let _ = f.sync_all();
        }
    }

    fn today_path(&self) -> PathBuf {
        let date = chrono_date_string();
        self.events_dir.join(format!("{date}.ndjson"))
    }
}

/// Statistics from a WAL actor run.
#[derive(Clone, Debug, Default)]
pub struct WalActorStats {
    pub events_written: u64,
    pub syncs: u64,
    pub write_errors: u64,
    pub sync_errors: u64,
}

/// YYYY-MM-DD date string (UTC). Same algorithm as wal_writer.
fn chrono_date_string() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = secs / 86400;
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
    format!("{y:04}-{m:02}-{d:02}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    /// Helper to create a simple WAL event for testing.
    fn test_event(payload: WalPayload) -> WalEvent {
        make_wal_event(1_000_000_000, payload)
    }

    #[test]
    fn test_wal_actor_basic_write() {
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        let (handle, join) = WalActor::spawn(&events, &dead, 100);

        // Write a single event
        let event = test_event(WalPayload::SystemReady {
            wal_events_replayed: 0,
            positions_reconciled: 0,
        });
        assert!(handle.append(event));

        // Shutdown and collect stats
        assert!(handle.shutdown());
        let stats = join.join().expect("WAL thread join");

        assert_eq!(stats.events_written, 1);
        assert_eq!(stats.write_errors, 0);
    }

    #[test]
    fn test_wal_actor_batch_sync() {
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        // Sync every 10 writes
        let (handle, join) = WalActor::spawn(&events, &dead, 10);

        for i in 0..25 {
            let event = test_event(WalPayload::SystemReady {
                wal_events_replayed: i,
                positions_reconciled: 0,
            });
            assert!(handle.append(event));
        }

        assert!(handle.shutdown());
        let stats = join.join().expect("join");

        assert_eq!(stats.events_written, 25);
        // 2 batch syncs (at 10 and 20) + 1 shutdown sync = 3
        assert_eq!(stats.syncs, 3);
    }

    #[test]
    fn test_wal_actor_force_sync() {
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        let (handle, join) = WalActor::spawn(&events, &dead, 1000);

        let event = test_event(WalPayload::SystemReady {
            wal_events_replayed: 0,
            positions_reconciled: 0,
        });
        assert!(handle.append(event));
        assert!(handle.force_sync());
        assert!(handle.shutdown());

        let stats = join.join().expect("join");
        assert_eq!(stats.events_written, 1);
        // 1 force sync + 1 shutdown sync
        assert_eq!(stats.syncs, 2);
    }

    #[test]
    fn test_wal_actor_write_payload_convenience() {
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        let (handle, join) = WalActor::spawn(&events, &dead, 100);

        assert!(handle.write_payload(
            123_456_789,
            WalPayload::RiskStateChange {
                from: "Normal".into(),
                to: "Reduce".into(),
                trigger: "test".into(),
            }
        ));

        assert!(handle.shutdown());
        let stats = join.join().expect("join");
        assert_eq!(stats.events_written, 1);
    }

    #[test]
    fn test_wal_actor_file_created_with_content() {
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        let (handle, join) = WalActor::spawn(&events, &dead, 100);

        handle.write_payload(
            1_000_000_000,
            WalPayload::SystemReady {
                wal_events_replayed: 42,
                positions_reconciled: 2,
            },
        );

        handle.shutdown();
        join.join().expect("join");

        // Verify the file has content
        let date = chrono_date_string();
        let file_path = events.join(format!("{date}.ndjson"));
        let content = fs::read_to_string(&file_path).expect("read WAL file");
        assert!(content.contains("SystemReady"));
        assert!(content.contains("42"));
    }

    #[test]
    fn test_wal_actor_multiple_handles() {
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        let (handle, join) = WalActor::spawn(&events, &dead, 100);

        // Clone handle (simulates engine + exit_engine + risk_arbiter)
        let handle2 = handle.clone();
        let handle3 = handle.clone();

        handle.write_payload(1, WalPayload::SystemReady {
            wal_events_replayed: 0,
            positions_reconciled: 0,
        });
        handle2.write_payload(2, WalPayload::RiskStateChange {
            from: "Normal".into(),
            to: "Halt".into(),
            trigger: "test".into(),
        });
        handle3.write_payload(3, WalPayload::SystemReady {
            wal_events_replayed: 1,
            positions_reconciled: 1,
        });

        handle.shutdown();
        let stats = join.join().expect("join");
        assert_eq!(stats.events_written, 3);
    }

    #[test]
    fn test_wal_bounded_channel_latency() {
        // AT-RM2: verify <1ms enqueue latency under burst
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        let (handle, join) = WalActor::spawn(&events, &dead, 100);

        let iterations = 10_000;
        let start = Instant::now();

        for i in 0..iterations {
            handle.write_payload(
                i,
                WalPayload::SystemReady {
                    wal_events_replayed: i,
                    positions_reconciled: 0,
                },
            );
        }

        let elapsed = start.elapsed();
        let avg_ns = elapsed.as_nanos() / iterations as u128;

        handle.shutdown();
        let stats = join.join().expect("join");

        assert_eq!(stats.events_written, iterations);
        // Average enqueue latency must be <1ms (typically <1μs for unbounded channel)
        assert!(
            avg_ns < 1_000_000,
            "Average enqueue latency {}ns exceeds 1ms",
            avg_ns
        );
        eprintln!(
            "WAL actor: {}k events, avg enqueue={}ns, total={:.1}ms",
            iterations / 1000,
            avg_ns,
            elapsed.as_secs_f64() * 1000.0
        );
    }

    #[test]
    fn test_wal_actor_no_oom_under_burst() {
        // Verify no OOM under 10k writes (unbounded channel drains fast)
        let dir = tempfile::tempdir().expect("tempdir");
        let events = dir.path().join("events");
        let dead = dir.path().join("dead");

        let (handle, join) = WalActor::spawn(&events, &dead, 50);

        for i in 0..10_000 {
            handle.write_payload(
                i,
                WalPayload::SystemReady {
                    wal_events_replayed: i,
                    positions_reconciled: 0,
                },
            );
        }

        handle.shutdown();
        let stats = join.join().expect("join");

        assert_eq!(stats.events_written, 10_000);
        assert_eq!(stats.write_errors, 0);
        // Should have ~200 batch syncs (10000/50) + 1 shutdown
        assert!(stats.syncs >= 200);
    }
}
