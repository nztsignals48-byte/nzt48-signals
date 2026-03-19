//! Phase 3 acceptance tests for WAL writer + replay.

#[cfg(test)]
mod tests {
    use crate::portfolio::PortfolioState;
    use crate::types::{WalEvent, WalPayload};
    use crate::wal_replay::{
        compute_state_hash, read_wal_file, replay_events, replay_from_snapshot,
    };
    use crate::wal_writer::{WalWriter, make_wal_event};
    use std::fs;
    use std::io::Write;

    fn make_routed_order(ticker_id: u32) -> WalPayload {
        WalPayload::RoutedOrder {
            order_id: format!("order-{ticker_id}"),
            ticker_id,
            side: "Long".into(),
            confidence: 75.0,
            strategy: "VanguardSniper".into(),
            kelly_fraction: 0.08,
            approved_size: 1000.0,
            symbol: format!("TEST{ticker_id}.L"),
            qty: 100,
            currency: "GBP".into(),
            entry_rvol: 0.0,
            entry_hurst: 0.0,
            entry_adx: 0.0,
                        }
    }

    fn make_fill(order_id: &str, ticker_id: u32, qty: u32, price: f64) -> WalPayload {
        WalPayload::FillEvent {
            order_id: order_id.into(),
            ticker_id,
            filled_qty: qty,
            remaining_qty: 0,
            price,
            exec_id: uuid::Uuid::now_v7().to_string(),
            commission: 1.50,
        }
    }

    fn write_events(
        writer: &mut WalWriter,
        count: usize,
        payload_fn: impl Fn(usize) -> WalPayload,
    ) {
        for i in 0..count {
            let event = make_wal_event(i as u64 * 1_000_000, payload_fn(i));
            writer.append(&event).expect("append failed");
        }
    }

    // ── Test 1: 100 events → kill → replay → state matches ──
    #[test]
    fn test_100_events_replay_state_matches() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        // Write 100 events (routed orders + fills for first 10)
        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        for i in 0..100u32 {
            let event = make_wal_event(i as u64 * 1_000_000, make_routed_order(i));
            writer.append(&event).expect("append");
        }
        // Add fills for first 3 tickers
        for i in 0..3u32 {
            let event = make_wal_event(
                200_000_000 + i as u64 * 1_000_000,
                make_fill("fill-order", i, 100, 10.0 + i as f64),
            );
            writer.append(&event).expect("append fill");
        }
        drop(writer);

        // "Restart" — read and replay
        let events = read_wal_file(&wal_path).expect("read");
        let mut portfolio = PortfolioState::new(100_000.0);
        let result = replay_events(&events, &mut portfolio);
        assert_eq!(result.events_replayed, 103);
        assert_eq!(portfolio.filled_count(), 3);
    }

    // ── Test 2: WAL line format ──
    #[test]
    fn test_wal_line_format() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let event = make_wal_event(
            42_000_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        writer.append(&event).expect("append");
        drop(writer);

        let content = fs::read_to_string(&wal_path).expect("read");
        let parsed: WalEvent = serde_json::from_str(content.trim()).expect("parse");
        assert!(!parsed.event_id.is_empty());
        assert_eq!(parsed.schema_version, 1);
        assert_eq!(parsed.event_time_ns, 42_000_000);
        assert!(parsed.write_time_ns > 0);
        assert!(parsed.checksum > 0);
    }

    // ── Test 3: Corrupt last line → skip with WARNING ──
    #[test]
    fn test_corrupt_last_line_skipped() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let event = make_wal_event(
            1_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        writer.append(&event).expect("append");
        drop(writer);

        // Append a truncated JSON line
        let mut f = fs::OpenOptions::new()
            .append(true)
            .open(&wal_path)
            .expect("open append");
        writeln!(f, "{{\"event_id\":\"broken\",\"schema_ver").expect("write");
        drop(f);

        let events = read_wal_file(&wal_path).expect("read");
        assert_eq!(events.len(), 1); // Only the valid event
    }

    // ── Test 4: Corrupt non-last line → panic! (H27) ──
    #[test]
    #[should_panic(expected = "FATAL: WAL corruption")]
    fn test_corrupt_non_last_line_panics() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");

        // Write: corrupt line, then valid line
        let mut f = fs::File::create(&wal_path).expect("create");
        writeln!(f, "{{\"event_id\":\"broken\"").expect("write corrupt");
        writeln!(f, "{{\"event_id\":\"valid\",\"schema_version\":1,\"event_time_ns\":0,\"write_time_ns\":0,\"checksum\":0,\"payload\":{{\"SystemReady\":{{\"wal_events_replayed\":0,\"positions_reconciled\":0}}}}}}").expect("write valid");
        drop(f);

        let _ = read_wal_file(&wal_path); // Should panic
    }

    // ── Test 5: CRC32 mismatch on non-last line → panic! ──
    #[test]
    #[should_panic(expected = "FATAL: WAL corruption")]
    fn test_crc32_mismatch_panics() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        // Write a valid event, then tamper with checksum
        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let event = make_wal_event(1_000, make_routed_order(1));
        writer.append(&event).expect("append");
        let event2 = make_wal_event(2_000, make_routed_order(2));
        writer.append(&event2).expect("append");
        drop(writer);

        // Tamper: replace checksum in first line
        let content = fs::read_to_string(&wal_path).expect("read");
        let lines: Vec<&str> = content.trim().split('\n').collect();
        let tampered =
            lines[0].replacen("\"checksum\":", "\"checksum\":99999,\"_old_checksum\":", 1);
        let mut f = fs::File::create(&wal_path).expect("create");
        writeln!(f, "{tampered}").expect("write");
        writeln!(f, "{}", lines[1]).expect("write");
        drop(f);

        let _ = read_wal_file(&wal_path); // Should panic
    }

    // ── Test 6: Orphan detection ──
    #[test]
    fn test_orphan_detection() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        // RoutedOrder with no BrokerAck
        let event = make_wal_event(1_000, make_routed_order(1));
        writer.append(&event).expect("append");
        drop(writer);

        let events = read_wal_file(&wal_path).expect("read");
        let mut portfolio = PortfolioState::new(10_000.0);
        let result = replay_events(&events, &mut portfolio);
        assert_eq!(result.orphaned_orders.len(), 1);
    }

    // ── Test 7: Snapshot + partial replay ──
    #[test]
    fn test_snapshot_partial_replay() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        // Write 50 events
        write_events(&mut writer, 50, |i| make_routed_order(i as u32));
        // Write snapshot
        let snapshot = make_wal_event(
            50_000_000,
            WalPayload::StateSnapshot {
                portfolio_json: "{}".into(),
                equity: 10_500.0,
                high_water: 10_500.0,
                hash: "abc123".into(),
            },
        );
        writer.append(&snapshot).expect("append snapshot");
        // Write 10 more events
        write_events(&mut writer, 10, |i| make_routed_order(100 + i as u32));
        drop(writer);

        let events = read_wal_file(&wal_path).expect("read");
        assert_eq!(events.len(), 61);

        let mut portfolio = PortfolioState::new(10_000.0);
        let result = replay_from_snapshot(&events, &mut portfolio);
        // Only replays from snapshot onward: 1 (snapshot) + 10 = 11
        assert_eq!(result.events_replayed, 11);
        assert!(result.last_snapshot_used);
        assert_eq!(portfolio.equity, 10_500.0);
    }

    // ── Test 8: Idempotent replay (H84) ──
    #[test]
    fn test_idempotent_replay() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let event_id = uuid::Uuid::now_v7().to_string();
        let event = WalEvent {
            event_id: event_id.clone(),
            schema_version: 1,
            event_time_ns: 1_000,
            write_time_ns: 0,
            checksum: 0,
            payload: make_fill("order-1", 42, 100, 10.50),
        };
        writer.append(&event).expect("append");
        // Write same exec_id again (duplicate)
        writer.append(&event).expect("append dup");
        drop(writer);

        let events = read_wal_file(&wal_path).expect("read");
        let mut portfolio = PortfolioState::new(100_000.0);
        let result = replay_events(&events, &mut portfolio);
        assert_eq!(result.events_replayed, 2);
        // Only one position despite two fill events (dedup by exec_id)
        assert_eq!(portfolio.filled_count(), 1);
    }

    // ── Test 9: Hourly state hash (H85) ──
    #[test]
    fn test_state_hash_deterministic() {
        let p1 = PortfolioState::new(10_000.0);
        let p2 = PortfolioState::new(10_000.0);
        let h1 = compute_state_hash(&p1);
        let h2 = compute_state_hash(&p2);
        assert_eq!(h1, h2);

        let p3 = PortfolioState::new(10_001.0);
        let h3 = compute_state_hash(&p3);
        assert_ne!(h1, h3);

        // Identical construction → identical hash
        let p4 = PortfolioState::new(10_000.0);
        let h4 = compute_state_hash(&p4);
        assert_eq!(h1, h4);
    }

    // ── Test 10: Disk space check (H25) ──
    #[test]
    fn test_disk_space_low_rejects() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        writer.disk_check_fn = Some(Box::new(|| 3.0)); // 3% free
        let event = make_wal_event(
            1_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        let result = writer.append(&event);
        assert!(result.is_err());
    }

    // ── Test 11: WAL writer takes &Event (immutable borrow, H26) ──
    #[test]
    fn test_immutable_borrow() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let event = make_wal_event(
            1_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        // event is borrowed immutably by append
        writer.append(&event).expect("append");
        // event is still usable after append
        assert_eq!(event.event_time_ns, 1_000);
    }

    // ── Test 12: Dead letter queue (H81) ──
    #[test]
    fn test_dead_letter_queue() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        writer
            .dead_letter("{\"broken\": \"intent\"}")
            .expect("dead letter");

        // Verify file exists in dead_letter/
        let entries: Vec<_> = fs::read_dir(&dl_path).expect("read dir").collect();
        assert!(!entries.is_empty());
    }

    // ── Test 13: Snapshot hash verification on replay (H85) ──
    #[test]
    fn test_snapshot_hash_in_replay() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let snapshot = make_wal_event(
            1_000,
            WalPayload::StateSnapshot {
                portfolio_json: "{}".into(),
                equity: 10_000.0,
                high_water: 10_000.0,
                hash: "deadbeef".into(),
            },
        );
        writer.append(&snapshot).expect("append");
        drop(writer);

        let events = read_wal_file(&wal_path).expect("read");
        let mut portfolio = PortfolioState::new(10_000.0);
        let result = replay_events(&events, &mut portfolio);
        assert_eq!(result.state_hash, Some("deadbeef".into()));
    }
}
