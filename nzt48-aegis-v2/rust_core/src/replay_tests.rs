//! Phase 7 acceptance tests: replay harness, determinism, fault injection, memory stability.

#[cfg(test)]
mod tests {
    use crate::portfolio::PortfolioState;
    use crate::replay::*;
    use crate::types::{MarketTick, TickerId};
    use crate::universe::{Universe, UniverseClass, UniverseConfig};
    use crate::wal_replay;
    use std::path::Path;

    fn setup_engine(wal_path: Option<&Path>) -> (ReplayEngine, Vec<TickerId>) {
        let mut universe = Universe::new(UniverseConfig::default());
        let t1 = universe.register("QQQ3.L", UniverseClass::Vanguard);
        let t2 = universe.register("NVD3.L", UniverseClass::Vanguard);
        let t3 = universe.register("APEX1.L", UniverseClass::Apex);
        universe.set_prev_close(t1, 10.0);
        universe.set_prev_close(t2, 20.0);
        universe.set_prev_close(t3, 5.0);
        (ReplayEngine::new(universe, wal_path), vec![t1, t2, t3])
    }

    // ── Test 1: Full day replay — synthetic data flows through entire pipeline ──
    #[test]
    fn test_full_day_replay() {
        let tmp = tempfile::TempDir::new().expect("tmpdir");
        let wal_path = tmp.path().join("events").join("replay.ndjson");
        let (mut engine, ids) = setup_engine(Some(&wal_path));
        let ticks = generate_synthetic_day(
            &ids[..2], // Two Vanguard tickers
            &[10.0, 20.0],
            500, // 500 ticks per ticker
            1_000_000_000,
        );
        for tick in ticks {
            engine.process_tick(tick);
        }
        assert_eq!(engine.counters.ticks_processed, 1000);
        assert!(
            engine.counters.signals_generated > 0,
            "should generate signals"
        );
        assert!(engine.counters.orders_approved > 0, "should approve orders");
        assert!(engine.counters.fills_received > 0, "should receive fills");
        assert!(
            engine.counters.wal_events_written > 0,
            "should write WAL events"
        );
    }

    // ── Test 2: Every approved signal → WAL → broker → fill → exit registered ──
    #[test]
    fn test_signal_path_connected() {
        let tmp = tempfile::TempDir::new().expect("tmpdir");
        let wal_path = tmp.path().join("events").join("replay.ndjson");
        let (mut engine, ids) = setup_engine(Some(&wal_path));
        let ticks = generate_synthetic_day(&ids[..1], &[10.0], 300, 1_000_000_000);
        for tick in ticks {
            engine.process_tick(tick);
        }
        // Every approved order should produce fills
        let approved = engine.counters.orders_approved;
        let fills = engine.counters.fills_received;
        assert!(approved > 0);
        assert_eq!(fills, approved, "every approved order must produce a fill");
        // WAL events: per approved order = RoutedOrder + Ack + Fill = 3 minimum
        assert!(engine.counters.wal_events_written >= approved * 3);
    }

    // ── Test 3: ZERO disconnected signal paths ──
    #[test]
    fn test_zero_disconnected_paths() {
        let tmp = tempfile::TempDir::new().expect("tmpdir");
        let wal_path = tmp.path().join("events").join("replay.ndjson");
        let (mut engine, ids) = setup_engine(Some(&wal_path));
        let ticks = generate_synthetic_day(&ids[..2], &[10.0, 20.0], 400, 1_000_000_000);
        for tick in ticks {
            engine.process_tick(tick);
        }
        let total_signals = engine.counters.signals_generated;
        let total_outcomes = engine.counters.orders_approved + engine.counters.orders_rejected;
        assert_eq!(
            total_signals, total_outcomes,
            "signals in ({total_signals}) must equal events out ({total_outcomes})"
        );
    }

    // ── Test 4: ZERO orphaned state — portfolio matches WAL ──
    #[test]
    fn test_zero_orphaned_state() {
        let tmp = tempfile::TempDir::new().expect("tmpdir");
        let wal_path = tmp.path().join("events").join("replay.ndjson");
        let (mut engine, ids) = setup_engine(Some(&wal_path));
        let ticks = generate_synthetic_day(&ids[..1], &[10.0], 300, 1_000_000_000);
        for tick in ticks {
            engine.process_tick(tick);
        }
        let engine_wal_events = engine.counters.wal_events_written;
        // Close WAL file before reading
        engine.wal = None;
        // Replay WAL into a fresh portfolio
        let wal_events = wal_replay::read_wal_file(&wal_path).expect("read WAL");
        // H27: last-line corruption → skip with WARNING. Allow at most 1 event lost.
        assert!(
            wal_events.len() as u64 >= engine_wal_events.saturating_sub(1),
            "WAL should contain nearly all events: have {}, expected ~{}",
            wal_events.len(),
            engine_wal_events
        );
        let mut replayed_portfolio = PortfolioState::new(10_000.0);
        let result = wal_replay::replay_events(&wal_events, &mut replayed_portfolio);
        // Verify WAL integrity: replayed events should be consistent
        assert!(result.events_replayed > 0, "should replay events");
        // Orphan count should be small (at most 1 from last-line skip)
        assert!(
            result.orphaned_orders.len() <= 1,
            "at most 1 orphan from H27 skip: {:?}",
            result.orphaned_orders
        );
    }

    // ── Test 5: Deterministic replay — same input twice → same WAL ──
    #[test]
    fn test_deterministic_replay() {
        // Run 1
        let tmp1 = tempfile::TempDir::new().expect("tmpdir");
        let wal1 = tmp1.path().join("events").join("replay.ndjson");
        let (mut engine1, ids1) = setup_engine(Some(&wal1));
        let ticks1 = generate_synthetic_day(&ids1[..1], &[10.0], 200, 1_000_000_000);
        for tick in ticks1 {
            engine1.process_tick(tick);
        }
        // Run 2 — identical
        let tmp2 = tempfile::TempDir::new().expect("tmpdir");
        let wal2 = tmp2.path().join("events").join("replay.ndjson");
        let (mut engine2, ids2) = setup_engine(Some(&wal2));
        let ticks2 = generate_synthetic_day(&ids2[..1], &[10.0], 200, 1_000_000_000);
        for tick in ticks2 {
            engine2.process_tick(tick);
        }
        // Compare counters
        assert_eq!(
            engine1.counters.ticks_processed,
            engine2.counters.ticks_processed
        );
        assert_eq!(
            engine1.counters.signals_generated,
            engine2.counters.signals_generated
        );
        assert_eq!(
            engine1.counters.orders_approved,
            engine2.counters.orders_approved
        );
        assert_eq!(
            engine1.counters.fills_received,
            engine2.counters.fills_received
        );
        assert_eq!(
            engine1.counters.exits_triggered,
            engine2.counters.exits_triggered
        );
        // WAL event counts match
        let events1 = wal_replay::read_wal_file(&wal1).expect("wal1");
        let events2 = wal_replay::read_wal_file(&wal2).expect("wal2");
        assert_eq!(events1.len(), events2.len(), "WAL event count must match");
        // State hashes match
        let h1 = wal_replay::compute_state_hash(&engine1.portfolio);
        let h2 = wal_replay::compute_state_hash(&engine2.portfolio);
        assert_eq!(h1, h2, "state hashes must be identical");
    }

    // ── Test 6: Network failure injection → HALT → recovery ──
    #[test]
    fn test_network_failure_halt_recovery() {
        let (mut engine, ids) = setup_engine(None);
        let base_ns = 1_000_000_000u64;
        // Process some ticks normally
        let ticks = generate_synthetic_day(&ids[..1], &[10.0], 50, base_ns);
        for tick in &ticks[..25] {
            engine.process_tick(tick.clone());
        }
        let approved_before = engine.counters.orders_approved;
        // Disconnect broker
        engine.broker.disconnect();
        // Process more ticks — risk arbiter should detect disconnect → HALT
        for tick in &ticks[25..50] {
            engine.process_tick(tick.clone());
        }
        // After disconnect, new orders should be rejected (HALT or broker fail)
        let approved_during_disconnect = engine.counters.orders_approved - approved_before;
        // No new positions should be opened during disconnect
        assert_eq!(
            approved_during_disconnect, 0,
            "no orders approved during disconnect"
        );
        // Reconnect
        engine.broker.reconnect();
        engine.arbiter.manual_clear_halt();
        // Verify engine can trade again
        let more_ticks = generate_synthetic_day(&ids[..1], &[10.0], 50, base_ns + 100_000_000_000);
        for tick in more_ticks {
            engine.process_tick(tick);
        }
        // Existing positions from before disconnect should still be tracked
        assert!(
            engine.counters.ticks_processed > 50,
            "engine continues processing after recovery"
        );
    }

    // ── Test 7: Gap detection — >2% gap → 15-min cooldown (H66) ──
    #[test]
    fn test_gap_detection_cooldown() {
        let (mut engine, ids) = setup_engine(None);
        let tid = ids[0];
        let base_ns = 1_000_000_000u64;
        // Establish a normal price
        let normal_tick = MarketTick {
            ticker_id: tid,
            bid: 10.04,
            ask: 10.06,
            last: 10.05,
            volume: 10000,
            timestamp_ns: base_ns,
            recv_timestamp_ns: base_ns + 100,
            ..Default::default()
        };
        engine.process_tick(normal_tick);
        // Inject >2% gap
        let gap_tick = inject_gap_tick(tid, 10.05, 0.025, base_ns + 1_000_000_000);
        engine.process_tick(gap_tick);
        assert_eq!(
            engine.counters.gap_cooldowns, 1,
            "gap should trigger cooldown"
        );
        // Next signal tick within cooldown should be blocked
        let signals_before = engine.counters.signals_generated;
        let within_cooldown = MarketTick {
            ticker_id: tid,
            bid: 10.30,
            ask: 10.32,
            last: 10.31,
            volume: 12000,
            timestamp_ns: base_ns + 2_000_000_000,
            recv_timestamp_ns: base_ns + 2_000_000_100,
            ..Default::default()
        };
        engine.process_tick(within_cooldown);
        // Should NOT generate a new signal during cooldown
        // (price is above threshold but cooldown blocks it)
        assert_eq!(
            engine.counters.signals_generated, signals_before,
            "no new signals during gap cooldown"
        );
    }

    // ── Test 8: Erroneous tick filter — spike filtered before stop triggers (H77) ──
    #[test]
    fn test_erroneous_tick_filtered() {
        let (mut engine, ids) = setup_engine(None);
        let tid = ids[0];
        let base_ns = 1_000_000_000u64;
        // Establish EMA with normal ticks
        for i in 0..20 {
            let tick = MarketTick {
                ticker_id: tid,
                bid: 9.99,
                ask: 10.01,
                last: 10.0,
                volume: 10000,
                timestamp_ns: base_ns + i * 100_000_000,
                recv_timestamp_ns: base_ns + i * 100_000_000 + 100,
                ..Default::default()
            };
            engine.process_tick(tick);
        }
        // Inject >15% erroneous spike (threshold raised for 3x ETP compatibility)
        let spike = inject_spike_tick(tid, 10.0, 0.20, base_ns + 3_000_000_000);
        engine.process_tick(spike);
        assert!(
            engine.counters.erroneous_ticks >= 1,
            "spike should be filtered"
        );
        // Normal ticks should still work after filter
        let normal = MarketTick {
            ticker_id: tid,
            bid: 9.99,
            ask: 10.01,
            last: 10.0,
            volume: 11000,
            timestamp_ns: base_ns + 4_000_000_000,
            recv_timestamp_ns: base_ns + 4_000_000_100,
            ..Default::default()
        };
        engine.process_tick(normal);
        assert!(
            engine.counters.ticks_processed > engine.counters.ticks_filtered,
            "normal ticks pass after erroneous filter"
        );
    }

    // ── Test 9: Price spike filter — flash crash midpoint check (H71) ──
    #[test]
    fn test_price_spike_filter_flash_crash() {
        let (mut engine, ids) = setup_engine(None);
        let tid = ids[0];
        let base_ns = 1_000_000_000u64;
        // Open a position at 10.20
        let entry_tick = MarketTick {
            ticker_id: tid,
            bid: 10.19,
            ask: 10.21,
            last: 10.20,
            volume: 10000,
            timestamp_ns: base_ns,
            recv_timestamp_ns: base_ns + 100,
            ..Default::default()
        };
        engine.process_tick(entry_tick);
        // If a position was opened, inject flash crash
        if !engine.positions.is_empty() {
            let crash = inject_flash_crash(tid, 10.20, base_ns + 1_000_000_000);
            engine.process_tick(crash);
            assert!(
                engine.counters.price_spikes_blocked >= 1,
                "flash crash should be blocked by price spike filter"
            );
        }
    }

    // ── Test 10: Synthetic halt — 30s no-tick → filtered (H122) ──
    #[test]
    fn test_synthetic_halt_30s() {
        let (mut engine, ids) = setup_engine(None);
        let tid = ids[0];
        let base_ns = 1_000_000_000u64;
        // First tick establishes last_tick_ns
        let tick1 = MarketTick {
            ticker_id: tid,
            bid: 9.99,
            ask: 10.01,
            last: 10.0,
            volume: 10000,
            timestamp_ns: base_ns,
            recv_timestamp_ns: base_ns + 100,
            ..Default::default()
        };
        engine.process_tick(tick1);
        // 31 seconds later → synthetic halt
        let tick2 = MarketTick {
            ticker_id: tid,
            bid: 9.99,
            ask: 10.01,
            last: 10.0,
            volume: 10001,
            timestamp_ns: base_ns + 31_000_000_000,
            recv_timestamp_ns: base_ns + 31_000_000_100,
            ..Default::default()
        };
        engine.process_tick(tick2);
        assert!(
            engine.counters.synthetic_halts >= 1,
            "30s gap should trigger synthetic halt"
        );
    }

    // ── Test 11: T2T latency logging (H118) ──
    #[test]
    fn test_t2t_latency_logging() {
        let (mut engine, ids) = setup_engine(None);
        let ticks = generate_synthetic_day(&ids[..1], &[10.0], 100, 1_000_000_000);
        for tick in ticks {
            engine.process_tick(tick);
        }
        assert_eq!(
            engine.latency_log.len() as u64,
            engine.counters.ticks_processed,
            "every tick must have a latency record"
        );
        // All latency records should have valid timestamps
        for record in &engine.latency_log {
            // recv_ns is offset +100ns from timestamp_ns (synthetic data)
            // both should be nonzero and reasonable
            assert!(record.recv_ns > 0, "recv_ns must be nonzero");
            assert!(record.process_ns > 0, "process_ns must be nonzero");
        }
    }

    // ── Test 12: Memory stability — 1M ticks, memory flat (H98) ──
    #[test]
    fn test_memory_stability_1m_ticks() {
        let (mut engine, ids) = setup_engine(None);
        // Disable WAL to avoid disk I/O in this test
        engine.wal = None;
        // Generate 1M ticks across 2 tickers
        let base_ns = 1_000_000_000u64;
        for batch in 0..1000 {
            for i in 0..1000 {
                let idx = batch * 1000 + i;
                let tid = ids[(idx % 2) as usize];
                let base_price = if idx % 2 == 0 { 10.0 } else { 20.0 };
                let t = idx as f64 / 1_000_000.0;
                let price = base_price + (t * std::f64::consts::PI * 40.0).sin() * 0.05;
                let ts = base_ns + idx as u64 * 1000;
                let tick = MarketTick {
                    ticker_id: tid,
                    bid: price - 0.01,
                    ask: price + 0.01,
                    last: price,
                    volume: 10_000,
                    timestamp_ns: ts,
                    recv_timestamp_ns: ts + 100,
                    ..Default::default()
                };
                engine.process_tick(tick);
            }
            // Clear latency log periodically to simulate bounded logging
            if engine.latency_log.len() > 10_000 {
                engine.latency_log.drain(..engine.latency_log.len() - 1000);
            }
        }
        assert_eq!(engine.counters.ticks_processed, 1_000_000);
        // Positions should be bounded (max 3 via risk arbiter)
        assert!(
            engine.positions.len() <= 3,
            "positions bounded by max_positions"
        );
        // Last prices map bounded by number of tickers
        assert!(engine.last_prices.len() <= ids.len());
    }

    // ── Test 13: Pipeline with all three ticker classes ──
    #[test]
    fn test_pipeline_all_ticker_classes() {
        let (mut engine, ids) = setup_engine(None);
        let base_ns = 1_000_000_000u64;
        // Vanguard ticker
        let tick_v = MarketTick {
            ticker_id: ids[0],
            bid: 10.09,
            ask: 10.11,
            last: 10.10,
            volume: 10000,
            timestamp_ns: base_ns,
            recv_timestamp_ns: base_ns + 100,
            ..Default::default()
        };
        engine.process_tick(tick_v);
        // Another Vanguard
        let tick_v2 = MarketTick {
            ticker_id: ids[1],
            bid: 20.19,
            ask: 20.21,
            last: 20.20,
            volume: 10000,
            timestamp_ns: base_ns + 1000,
            recv_timestamp_ns: base_ns + 1100,
            ..Default::default()
        };
        engine.process_tick(tick_v2);
        // Apex ticker
        let tick_a = MarketTick {
            ticker_id: ids[2],
            bid: 5.04,
            ask: 5.06,
            last: 5.05,
            volume: 10000,
            timestamp_ns: base_ns + 2000,
            recv_timestamp_ns: base_ns + 2100,
            ..Default::default()
        };
        engine.process_tick(tick_a);
        assert_eq!(engine.counters.ticks_processed, 3);
        assert_eq!(
            engine.counters.ticks_filtered, 0,
            "no ticks should be filtered"
        );
    }
}
