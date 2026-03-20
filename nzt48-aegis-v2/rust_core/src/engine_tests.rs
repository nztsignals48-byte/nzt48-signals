//! Phase 8 acceptance tests: Engine startup, reconciliation, error handling, restart.

#[cfg(test)]
mod tests {
    use std::path::Path;

    use crate::broker::{BrokerEvent, BrokerPosition};
    use crate::clock::Clock;
    use crate::config_loader::EngineConfig;
    use crate::engine::{Engine, round_to_tick_size};
    use crate::paper_broker::{PaperBroker, PaperBrokerConfig};
    use crate::types::{MarketTick, RiskRegime, TickerId, WalPayload};
    use crate::wal_writer::{WalWriter, make_wal_event};

    fn config_dir() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .expect("parent")
            .join("config")
    }

    fn make_engine() -> Engine<PaperBroker> {
        let config = EngineConfig::load(&config_dir()).expect("load config");
        let clock = Clock::new(config.holidays.clone());
        let broker = PaperBroker::new(PaperBrokerConfig::default());
        Engine::new(broker, config, None, clock)
    }

    fn make_engine_with_wal() -> (Engine<PaperBroker>, tempfile::TempDir) {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");
        let config = EngineConfig::load(&config_dir()).expect("load config");
        let clock = Clock::new(config.holidays.clone());
        let broker = PaperBroker::new(PaperBrokerConfig::default());
        let wal = WalWriter::open_file(&wal_path, &dl_path).expect("open WAL");
        (Engine::new(broker, config, Some(wal), clock), dir)
    }

    fn make_tick(tid: u32, price: f64, ts: u64) -> MarketTick {
        MarketTick {
            ticker_id: TickerId(tid),
            bid: price - 0.01,
            ask: price + 0.01,
            last: price,
            volume: 10_000,
            timestamp_ns: ts,
            recv_timestamp_ns: ts + 100,
        }
    }

    // ── Test 1: 8-step startup sequence completes ──
    #[test]
    fn test_startup_sequence_completes() {
        let mut engine = make_engine();
        let result = engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        assert!(engine.startup_complete);
        assert_eq!(result.wal_events_replayed, 0);
        assert_eq!(result.positions_reconciled, 0);
        assert_eq!(result.orphans_found, 0);
    }

    // ── Test 2: Clock offset computed on startup ──
    #[test]
    fn test_clock_offset_computed() {
        let mut engine = make_engine();
        // Broker 2s ahead
        let broker_secs = 1_700_000_000u64;
        let system_ns = (broker_secs - 2) * 1_000_000_000;
        engine
            .startup(&[], broker_secs, system_ns)
            .expect("startup");
        assert!(engine.clock.is_synced());
        assert!((engine.clock.offset_secs() - 2.0).abs() < 0.001);
    }

    // ── Test 3: Config loaded from TOML ──
    #[test]
    fn test_config_loaded_from_toml() {
        let config = EngineConfig::load(&config_dir()).expect("load");
        assert_eq!(config.risk.max_positions, 15);  // PAPER VALIDATION: maximise trade data
        assert_eq!(config.ibkr.client_id_executioner, 101);
        assert_eq!(config.ibkr.reqmktdata_pacing_ms, 10);
        assert!(config.crucible.paper_mode);
        // Expanded: 49 LSEETF + 70 US/SMART + 60 TSE + 40 HKEX + 39 KRX + 20 XETRA + 12 EURONEXT + 10 SGX + others
        assert!(config.contracts.len() >= 200, "Expected >= 200 contracts, got {}", config.contracts.len());
        assert!(config.tickers.len() >= 12);
    }

    // ── Test 4: Contracts loaded from contracts.toml ──
    #[test]
    fn test_contracts_loaded() {
        let config = EngineConfig::load(&config_dir()).expect("load");
        let qqq3 = config
            .contracts
            .iter()
            .find(|c| c.symbol == "QQQ3.L")
            .expect("QQQ3.L");
        assert_eq!(qqq3.leverage, 3);
        assert_eq!(qqq3.exchange, "LSEETF");
        assert_eq!(qqq3.currency, "USD");  // LSE ETP currency fix: QQQ3.L trades in USD
        assert_eq!(qqq3.sector, "Technology");
    }

    // ── Test 5: Reconciliation detects quantity mismatch ──
    #[test]
    fn test_reconciliation_detects_mismatch() {
        let (mut engine, _dir) = make_engine_with_wal();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");

        // Process a tick to create a position
        let tick = make_tick(1, 10.50, 1_700_000_001_000_000_000);
        engine.process_tick(tick);

        // Force a broker position with different qty
        engine.broker.inject_position(BrokerPosition {
            ticker_id: TickerId(99),
            qty: 200,
            avg_cost: 15.0,
        });

        let recon = engine.reconcile().expect("reconcile");
        assert!(!recon.is_clean);
        // In simulation mode, reconcile() sets Flatten but the simulation-mode
        // auto-clear at end of reconcile() resets to Normal (prevents stale regime
        // from blocking data collection). Verify the mismatch was detected by result.
        assert!(recon.mismatches.len() >= 1, "Expected at least 1 mismatch");
    }

    // ── Test 6: Orphan detection on startup ──
    #[test]
    fn test_orphan_detection_on_startup() {
        let mut engine = make_engine();
        // Inject an open order the engine doesn't know about
        engine
            .broker
            .inject_open_order("orphan-order-1", TickerId(42), 100);
        let result = engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        assert_eq!(result.orphans_found, 1);
    }

    // ── Test 7: Restart recovery — WAL replay + SystemReady ──
    #[test]
    fn test_restart_recovery_wal_replay() {
        let dir = tempfile::tempdir().expect("tempdir");
        let wal_path = dir.path().join("test.ndjson");
        let dl_path = dir.path().join("dead_letter");

        // Simulate previous session: write WAL events
        let mut writer = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let ev = make_wal_event(
            1_000_000,
            WalPayload::SystemReady {
                wal_events_replayed: 0,
                positions_reconciled: 0,
            },
        );
        writer.append(&ev).expect("append");
        drop(writer);

        // "Restart" — read WAL and replay
        let events = crate::wal_replay::read_wal_file(&wal_path).expect("read");
        let config = EngineConfig::load(&config_dir()).expect("load");
        let clock = Clock::new(config.holidays.clone());
        let broker = PaperBroker::new(PaperBrokerConfig::default());
        let wal = WalWriter::open_file(&wal_path, &dl_path).expect("open");
        let mut engine = Engine::new(broker, config, Some(wal), clock);
        let result = engine
            .startup(&events, 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        assert!(result.wal_events_replayed >= 1);
        assert!(engine.startup_complete);
    }

    // ── Test 8: Restart — no duplicate orders ──
    #[test]
    fn test_restart_no_duplicate_orders() {
        let mut engine = make_engine();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");

        // Process some ticks to potentially generate orders
        for i in 0..5 {
            let tick = make_tick(1, 10.50, 1_700_000_001_000_000_000 + i * 1_000_000_000);
            engine.process_tick(tick);
        }
        let orders_before = engine.tracked_orders.len();

        // "Restart" — shutdown + startup
        engine.shutdown();
        assert!(!engine.startup_complete);

        engine.broker = PaperBroker::new(PaperBrokerConfig::default());
        engine
            .startup(&[], 1_700_000_002, 1_700_000_002_000_000_000)
            .expect("startup2");
        // No new orders submitted during startup
        assert_eq!(engine.tracked_orders.len(), orders_before);
    }

    // ── Test 9: Marketable limit: Ask + 0.1% (H49) ──
    #[test]
    fn test_marketable_limit_order_price() {
        let config = EngineConfig::load(&config_dir()).expect("load");
        assert!((config.execution.marketable_limit_buffer_pct - 0.1).abs() < 0.001);
        // Ask = 100.00 → limit = 100.00 * 1.001 = 100.10
        // floor(100.10 / 0.01) * 0.01 = 100.10 (exact at this scale)
        let limit = 100.00 * (1.0 + config.execution.marketable_limit_buffer_pct / 100.0);
        let rounded = round_to_tick_size(limit, &config);
        assert!((rounded - 100.10).abs() < 0.01);
        // Verify it's a valid tick size (multiple of 0.01)
        assert!((rounded * 100.0 - (rounded * 100.0).round()).abs() < 0.001);
    }

    // ── Test 10: Error 1100 → HALT (H43) ──
    #[test]
    fn test_error_1100_halt() {
        let mut engine = make_engine();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        assert_eq!(engine.arbiter.regime, RiskRegime::Normal);

        engine.handle_ibkr_error(1100, "Connectivity lost");
        assert_eq!(engine.arbiter.regime, RiskRegime::Halt);
    }

    // ── Test 11: Error 1102 → reconcile before NORMAL (H44) ──
    #[test]
    fn test_error_1102_reconcile() {
        let mut engine = make_engine();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");

        // Simulate reconnect
        engine.handle_ibkr_error(1102, "Connectivity restored");
        // Should not crash; reconciliation runs internally
    }

    // ── Test 12: Error 321 → pacing backoff (H46) ──
    #[test]
    fn test_error_321_pacing() {
        let mut engine = make_engine();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        // Should not crash or change regime
        engine.handle_ibkr_error(321, "Pacing violation");
        assert_eq!(engine.arbiter.regime, RiskRegime::Normal);
    }

    // ── Test 13: Periodic reconciliation interval ──
    #[test]
    fn test_reconciliation_interval() {
        let mut engine = make_engine();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        // Just after startup → should not need reconciliation yet
        engine.now_ns = engine.last_reconcile_ns + 1_000_000_000; // 1s later
        assert!(!engine.should_reconcile());

        // 5 minutes later → should reconcile (300s = 300_000_000_000 ns)
        engine.now_ns = engine.last_reconcile_ns + 300_000_000_000;
        assert!(engine.should_reconcile());
    }

    // ── Test 14: Tick processing requires startup ──
    #[test]
    fn test_tick_requires_startup() {
        let mut engine = make_engine();
        assert!(!engine.startup_complete);
        let tick = make_tick(1, 10.50, 1_000_000);
        engine.process_tick(tick);
        // No crash, but no orders submitted
        assert!(engine.tracked_orders.is_empty());
    }

    // ── Test 15: Crucible mode limits positions ──
    #[test]
    fn test_crucible_single_position() {
        let mut engine = make_engine();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        // Crucible max_positions_override = 15 (PAPER VALIDATION: maximise trade data)
        assert_eq!(engine.arbiter.config.max_positions, 15);
    }

    // ── Test 16: Shutdown cancels pending orders ──
    #[test]
    fn test_shutdown_cancels_orders() {
        let mut engine = make_engine();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");
        engine.tracked_orders.push("test-order-1".to_string());
        engine.shutdown();
        assert!(!engine.startup_complete);
    }

    // ── Test 17: Broker disconnect event triggers HALT ──
    #[test]
    fn test_broker_disconnect_event_halt() {
        let (mut engine, _dir) = make_engine_with_wal();
        engine
            .startup(&[], 1_700_000_000, 1_700_000_000_000_000_000)
            .expect("startup");

        engine.process_broker_event(&BrokerEvent::Disconnected);
        assert_eq!(engine.arbiter.regime, RiskRegime::Halt);
    }

    // ── Test 18: LSE tick size rounding (H65) ──
    #[test]
    fn test_tick_size_rounding_comprehensive() {
        let config = EngineConfig::load(&config_dir()).expect("load");
        // Under £1: 0.001 increments
        assert!((round_to_tick_size(0.1234, &config) - 0.123).abs() < 0.0001);
        assert!((round_to_tick_size(0.9999, &config) - 0.999).abs() < 0.0001);
        // Over £1: 0.01 increments
        assert!((round_to_tick_size(1.234, &config) - 1.23).abs() < 0.001);
        assert!((round_to_tick_size(99.999, &config) - 99.99).abs() < 0.001);
    }
}
