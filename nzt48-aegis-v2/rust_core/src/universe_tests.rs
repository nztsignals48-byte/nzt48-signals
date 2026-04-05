//! Phase 6A acceptance tests for Universe data routing and channel.

#[cfg(test)]
mod tests {
    use crate::channel::{ChannelConfig, TickChannel};
    use crate::types::{MarketTick, RiskRegime, TickerId};
    use crate::universe::{FilterReason, RouteResult, Universe, UniverseClass, UniverseConfig};

    fn make_tick(ticker_id: TickerId, last: f64) -> MarketTick {
        MarketTick {
            ticker_id,
            bid: last - 0.01,
            ask: last + 0.01,
            last,
            volume: 1000,
            timestamp_ns: 1_000_000_000,
            recv_timestamp_ns: 1_000_000_100,
            bid_size: 0,
            ask_size: 0,
        }
    }

    // ── Test 1: Feed 1,000 tickers → Vanguard (300) + Apex (700) ──
    #[test]
    fn test_1000_tickers_vanguard_apex_split() {
        let mut universe = Universe::new(UniverseConfig::default());
        let mut vanguard_ids = Vec::new();
        let mut apex_ids = Vec::new();
        // Register 300 Vanguard + 700 Apex
        for i in 0..300 {
            let id = universe.register(&format!("VANG{i}.L"), UniverseClass::Vanguard);
            universe.set_prev_close(id, 10.0);
            vanguard_ids.push(id);
        }
        for i in 0..700 {
            let id = universe.register(&format!("APEX{i}.L"), UniverseClass::Apex);
            universe.set_prev_close(id, 10.0);
            apex_ids.push(id);
        }
        assert_eq!(universe.intern.len(), 1000);

        // Route ticks and verify classification
        let mut vanguard_count = 0;
        let mut apex_count = 0;
        let now = 1_500_000_000u64;
        for &id in &vanguard_ids {
            let tick = make_tick(id, 10.05);
            match universe.route_tick(&tick, now) {
                RouteResult::Vanguard(_) => vanguard_count += 1,
                other => panic!("Vanguard ticker routed wrong: {other:?}"),
            }
        }
        for &id in &apex_ids {
            let tick = make_tick(id, 10.05);
            match universe.route_tick(&tick, now) {
                RouteResult::Apex(_) => apex_count += 1,
                other => panic!("Apex ticker routed wrong: {other:?}"),
            }
        }
        assert_eq!(vanguard_count, 300);
        assert_eq!(apex_count, 700);
    }

    // ── Test 2: NO tick routed to both Vanguard AND Apex ──
    #[test]
    fn test_exclusive_routing() {
        let mut universe = Universe::new(UniverseConfig::default());
        let v_id = universe.register("QQQ3.L", UniverseClass::Vanguard);
        let a_id = universe.register("APEX1.L", UniverseClass::Apex);
        universe.set_prev_close(v_id, 10.0);
        universe.set_prev_close(a_id, 10.0);
        let now = 1_000_000_000u64;

        // Vanguard ticker ONLY goes to Vanguard
        let tick = make_tick(v_id, 10.01);
        assert!(matches!(
            universe.route_tick(&tick, now),
            RouteResult::Vanguard(_)
        ));

        // Apex ticker ONLY goes to Apex
        let tick = make_tick(a_id, 10.01);
        assert!(matches!(
            universe.route_tick(&tick, now),
            RouteResult::Apex(_)
        ));
    }

    // ── Test 3: Amihud illiquidity filter ──
    #[test]
    fn test_amihud_filter() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id = universe.register("ILLIQ.L", UniverseClass::Vanguard);
        universe.set_prev_close(id, 10.0);
        // Set Amihud above threshold (default 1.0)
        universe.set_amihud(id, 1.5);
        let tick = make_tick(id, 10.01);
        let result = universe.route_tick(&tick, 1_000_000_000);
        assert!(matches!(
            result,
            RouteResult::Filtered(FilterReason::AmihudIlliquid)
        ));
    }

    // ── Test 4: ASER filter (spread > 0.5%) ──
    #[test]
    fn test_aser_filter() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id = universe.register("WIDE.L", UniverseClass::Vanguard);
        universe.set_prev_close(id, 10.0);
        // Set ASER above threshold (default 0.005 = 0.5%)
        universe.set_aser(id, 0.008);
        let tick = make_tick(id, 10.01);
        let result = universe.route_tick(&tick, 1_000_000_000);
        assert!(matches!(
            result,
            RouteResult::Filtered(FilterReason::AserSpreadTooWide)
        ));
    }

    // ── Test 5: TickerId interning — no String in hot path (H01) ──
    #[test]
    fn test_ticker_interning() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id1 = universe.register("QQQ3.L", UniverseClass::Vanguard);
        let id2 = universe.register("QQQS.L", UniverseClass::Apex);
        // Same symbol returns same ID (idempotent)
        let id1b = universe.intern.intern("QQQ3.L");
        assert_eq!(id1, id1b);
        assert_ne!(id1, id2);
        // Reverse lookup
        assert_eq!(universe.intern.lookup(id1), Some("QQQ3.L"));
        assert_eq!(universe.intern.lookup(id2), Some("QQQS.L"));
        // Hot path uses TickerId(u32), NOT String
        assert_eq!(id1, TickerId(0));
        assert_eq!(id2, TickerId(1));
    }

    // ── Test 6: Crossbeam channel bounded at 50,000 ──
    #[test]
    fn test_channel_bounded_capacity() {
        let config = ChannelConfig {
            capacity: 100, // Small for testing
            ..Default::default()
        };
        let mut channel = TickChannel::new(config);
        let tick = make_tick(TickerId(1), 10.0);
        // Fill to capacity
        for _ in 0..100 {
            channel.send_or_drop_oldest(tick.clone(), 0);
        }
        assert_eq!(channel.len(), 100);
    }

    // ── Test 7: Oldest-tick dropping ──
    #[test]
    fn test_oldest_tick_dropping() {
        let config = ChannelConfig {
            capacity: 3,
            ..Default::default()
        };
        let mut channel = TickChannel::new(config);
        // Send ticks with different prices to distinguish them
        channel.send_or_drop_oldest(make_tick(TickerId(1), 1.0), 0);
        channel.send_or_drop_oldest(make_tick(TickerId(1), 2.0), 0);
        channel.send_or_drop_oldest(make_tick(TickerId(1), 3.0), 0);
        // Channel is full. Send a 4th — oldest (1.0) should be dropped
        let no_drop = channel.send_or_drop_oldest(make_tick(TickerId(1), 4.0), 100);
        assert!(!no_drop); // Returns false when dropping
        assert_eq!(channel.len(), 3);
        // Drain and verify: should be 2.0, 3.0, 4.0 (oldest 1.0 dropped)
        let batch = channel.recv_batch(10);
        assert_eq!(batch.len(), 3);
        assert!((batch[0].last - 2.0).abs() < 0.001);
        assert!((batch[1].last - 3.0).abs() < 0.001);
        assert!((batch[2].last - 4.0).abs() < 0.001);
    }

    // ── Test 8: Drop rate monitoring → REDUCE ──
    #[test]
    fn test_drop_rate_monitoring() {
        let config = ChannelConfig {
            capacity: 1,
            drop_alert_per_sec: 3,
            ..Default::default()
        };
        let mut channel = TickChannel::new(config);
        let tick = make_tick(TickerId(1), 10.0);
        // Fill channel
        channel.send_or_drop_oldest(tick.clone(), 0);
        // Drop 3 ticks in same second window → should trigger REDUCE
        let now = 1_000_000_000u64;
        channel.send_or_drop_oldest(tick.clone(), now);
        channel.send_or_drop_oldest(tick.clone(), now + 100);
        channel.send_or_drop_oldest(tick.clone(), now + 200);
        assert_eq!(channel.monitor.drops_this_second, 3);
        assert!(matches!(channel.check_health(), Some(RiskRegime::Reduce)));
    }

    // ── Test 9: Queue depth monitoring → REDUCE at 40k, HALT at 50k ──
    #[test]
    fn test_queue_depth_monitoring() {
        // Test REDUCE threshold
        let config = ChannelConfig {
            capacity: 50_000,
            reduce_threshold: 100,
            halt_threshold: 200,
            drop_alert_per_sec: 1000,
        };
        let mut channel = TickChannel::new(config);
        let tick = make_tick(TickerId(1), 10.0);
        // Fill to 99 — no escalation
        for _ in 0..99 {
            channel.send_or_drop_oldest(tick.clone(), 0);
        }
        assert!(channel.check_health().is_none());
        // Fill to 100 — REDUCE
        channel.send_or_drop_oldest(tick.clone(), 0);
        assert!(matches!(channel.check_health(), Some(RiskRegime::Reduce)));
        // Fill to 200 — HALT
        for _ in 0..100 {
            channel.send_or_drop_oldest(tick.clone(), 0);
        }
        assert!(matches!(channel.check_health(), Some(RiskRegime::Halt)));
    }

    // ── Test 10: Apex snapshot 60s interval (H18) ──
    #[test]
    fn test_apex_snapshot_interval() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id = universe.register("APEX1.L", UniverseClass::Apex);
        // Initially due (last_apex_snapshot_ns = 0)
        assert!(universe.apex_snapshot_due(id, 60_000_000_001));
        // Record snapshot
        universe.record_apex_snapshot(id, 60_000_000_000);
        // Not due at 119s
        assert!(!universe.apex_snapshot_due(id, 119_000_000_000));
        // Due at 120s (60s after last snapshot)
        assert!(universe.apex_snapshot_due(id, 120_000_000_001));
    }

    // ── Test 11: reqMktData pacing 10ms (H42) ──
    #[test]
    fn test_mkt_data_pacing() {
        let mut universe = Universe::new(UniverseConfig::default());
        // First request always allowed
        assert!(universe.can_request_mkt_data(0));
        universe.record_mkt_data_request(0);
        // 5ms later — too soon
        assert!(!universe.can_request_mkt_data(5_000_000));
        // 10ms later — OK
        assert!(universe.can_request_mkt_data(10_000_000));
    }

    // ── Test 12: Synthetic halt detection — 30s no ticks (H122) ──
    #[test]
    fn test_synthetic_halt_detection() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id = universe.register("DEAD.L", UniverseClass::Vanguard);
        universe.set_prev_close(id, 10.0);
        // First tick at t=1s
        let tick = make_tick(id, 10.01);
        assert!(matches!(
            universe.route_tick(&tick, 1_000_000_000),
            RouteResult::Vanguard(_)
        ));
        // Tick at t=30s — still within 30s window
        let tick = make_tick(id, 10.02);
        assert!(matches!(
            universe.route_tick(&tick, 30_000_000_000),
            RouteResult::Vanguard(_)
        ));
        // No tick until t=61s (31s since last tick at 30s) → synthetic halt
        let tick = make_tick(id, 10.03);
        assert!(matches!(
            universe.route_tick(&tick, 61_000_000_001),
            RouteResult::Filtered(FilterReason::SyntheticHalt)
        ));
    }

    // ── Test 13: Reverse split detection — >500% overnight move (H76) ──
    #[test]
    fn test_reverse_split_detection() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id = universe.register("SPLIT.L", UniverseClass::Vanguard);
        universe.set_prev_close(id, 10.0);
        // Normal tick at 10.05 — fine
        let tick = make_tick(id, 10.05);
        assert!(matches!(
            universe.route_tick(&tick, 1_000_000_000),
            RouteResult::Vanguard(_)
        ));
        // Extreme move: prev_close=10, now=65 (550% up) → reverse split
        let tick = make_tick(id, 65.0);
        let result = universe.route_tick(&tick, 2_000_000_000);
        assert!(matches!(
            result,
            RouteResult::Filtered(FilterReason::ReverseSplit)
        ));
        // Ticker is now halted — all subsequent ticks filtered
        let tick = make_tick(id, 10.0);
        assert!(matches!(
            universe.route_tick(&tick, 3_000_000_000),
            RouteResult::Filtered(FilterReason::TickerHalted)
        ));
    }

    // ── Test 14: Erroneous tick filter — >15% from 1s MA (H77) ──
    // Threshold raised from 5% to 15% to accommodate 3x leveraged ETPs.
    #[test]
    fn test_erroneous_tick_filter() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id = universe.register("ERR.L", UniverseClass::Vanguard);
        universe.set_prev_close(id, 10.0);
        // Normal tick to establish EMA
        let tick = make_tick(id, 10.0);
        assert!(matches!(
            universe.route_tick(&tick, 1_000_000_000),
            RouteResult::Vanguard(_)
        ));
        // 6% deviation now PASSES (below 15% threshold) — needed for 3x ETPs
        let tick = make_tick(id, 10.60);
        assert!(matches!(
            universe.route_tick(&tick, 2_000_000_000),
            RouteResult::Vanguard(_)
        ));
        // Erroneous tick: 16%+ deviation from EMA still filtered
        let tick = make_tick(id, 12.20);
        assert!(matches!(
            universe.route_tick(&tick, 3_000_000_000),
            RouteResult::Filtered(FilterReason::ErroneousTick)
        ));
        // Normal tick still works after erroneous filter
        let tick = make_tick(id, 10.10);
        assert!(matches!(
            universe.route_tick(&tick, 4_000_000_000),
            RouteResult::Vanguard(_)
        ));
    }

    // ── Test 15: Batch receive from channel ──
    #[test]
    fn test_channel_batch_recv() {
        let config = ChannelConfig {
            capacity: 1000,
            ..Default::default()
        };
        let mut channel = TickChannel::new(config);
        let tick = make_tick(TickerId(1), 10.0);
        for _ in 0..250 {
            channel.send_or_drop_oldest(tick.clone(), 0);
        }
        // Batch of 200
        let batch = channel.recv_batch(200);
        assert_eq!(batch.len(), 200);
        assert_eq!(channel.len(), 50);
        // Drain remaining
        let rest = channel.recv_batch(200);
        assert_eq!(rest.len(), 50);
        assert!(channel.is_empty());
    }

    // ── Test 16: Drop counter tracks total drops ──
    #[test]
    fn test_drop_counter() {
        let config = ChannelConfig {
            capacity: 1,
            ..Default::default()
        };
        let mut channel = TickChannel::new(config);
        let tick = make_tick(TickerId(1), 10.0);
        channel.send_or_drop_oldest(tick.clone(), 0);
        // 5 more sends → 5 drops
        for i in 1..=5 {
            channel.send_or_drop_oldest(tick.clone(), i * 100);
        }
        assert_eq!(channel.monitor.total_drops, 5);
    }

    // ── Test 17: ASER and Amihud below threshold → tick passes through ──
    #[test]
    fn test_filters_below_threshold_pass() {
        let mut universe = Universe::new(UniverseConfig::default());
        let id = universe.register("CLEAN.L", UniverseClass::Vanguard);
        universe.set_prev_close(id, 10.0);
        universe.set_amihud(id, 0.5); // Below 1.0 threshold
        universe.set_aser(id, 0.003); // Below 0.005 threshold
        let tick = make_tick(id, 10.01);
        assert!(matches!(
            universe.route_tick(&tick, 1_000_000_000),
            RouteResult::Vanguard(_)
        ));
    }
}
